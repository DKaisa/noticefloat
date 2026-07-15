package com.example.noticefloat.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.graphics.PixelFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.ImageView
import android.widget.TextView
import androidx.core.app.NotificationCompat
import androidx.recyclerview.widget.LinearLayoutManager
import com.example.noticefloat.NoticeApp
import com.example.noticefloat.R
import com.example.noticefloat.data.Task
import com.example.noticefloat.databinding.ViewFloatingPanelBinding
import com.example.noticefloat.remote.Session
import com.example.noticefloat.remote.WsClient
import com.example.noticefloat.ui.MainActivity
import com.example.noticefloat.ui.PublisherActivity
import com.example.noticefloat.ui.TaskAdapter
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.abs

/**
 * 悬浮窗前台服务：
 * - 常驻通知，避免被系统杀死
 * - 提供悬浮球（点击展开待办面板）
 * - 支持"仅微信前台时显示"或"全程显示"两种模式
 */
class FloatingService : Service() {

    companion object {
        private const val CHANNEL_ID = "notice_float_fg"
        private const val NOTIF_ID = 1001
        /** v0.8.10：无悬浮窗时也能看到消息 —— 收到 task.new 时发系统通知 */
        private const val INCOMING_CHANNEL_ID = "notice_float_incoming"
        /** v0.8.13：紧急消息专用高优先通道（伴 fullScreenIntent，绕过勿扰，锁屏可见） */
        private const val INCOMING_URGENT_CHANNEL_ID = "notice_float_incoming_urgent"
        private const val NOTIF_TAG_INCOMING = "incoming"
        const val PREF_NAME = "notice_float_prefs"
        const val KEY_ONLY_WHEN_WECHAT = "only_when_wechat"
        const val KEY_BUBBLE_STYLE = "bubble_style"
        const val KEY_LAST_SYNC_MS = "last_sync_ms"
        const val STYLE_BALL = "ball"
        const val STYLE_EDGE = "edge"

        fun start(context: Context) {
            val i = Intent(context, FloatingService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(i)
            } else {
                context.startService(i)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, FloatingService::class.java))
        }

        const val ACTION_RELOAD = "com.example.noticefloat.action.RELOAD_BUBBLE"
        /** v0.8.10：打开发布界面（PublisherActivity）时，令 Service 收起快捷发布悬浮面板，避免遮挡 */
        const val ACTION_HIDE_QUICK_PANEL = "com.example.noticefloat.action.HIDE_QUICK_PANEL"
        fun hideQuickPanel(context: Context) {
            val i = Intent(context, FloatingService::class.java).apply { action = ACTION_HIDE_QUICK_PANEL }
            runCatching {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(i)
                else context.startService(i)
            }
        }

        fun reload(context: Context) {
            val i = Intent(context, FloatingService::class.java).apply { action = ACTION_RELOAD }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(i)
            } else {
                context.startService(i)
            }
        }
    }

    private lateinit var wm: WindowManager
    private lateinit var prefs: SharedPreferences
    private var bubbleView: View? = null
    private var bubbleParams: WindowManager.LayoutParams? = null
    private var bubbleStyle: String = STYLE_BALL
    private var panelView: View? = null
    private var panelParams: WindowManager.LayoutParams? = null

    private var pendingCount: Int = 0
    private var isPanelOpen = false

    // v0.8.2 语音输入
    private var voiceRecognizer: android.speech.SpeechRecognizer? = null
    private var voiceListening = false
    private var voiceGotResult = false
    // v0.8.6: 若为 true，识别结果注入到 quickPublish EditText，不再打开 PublisherActivity
    private var voiceIntoQuickPanel = false
    private var voiceFallbackTried = false
    // v0.8.12：语言/引擎兜底重试标志
    private var voiceOnlineFallbackTried = false

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var detectJob: Job? = null
    private var ws: WsClient? = null

    // 自动缩回边缘条 定时器
    private val collapseHandler = Handler(Looper.getMainLooper())
    private val collapseRunnable = Runnable {
        // 若基准样式仍为 EDGE 且当前展示为 BALL，则缩回 EDGE
        if (baseStyle() == STYLE_EDGE && bubbleStyle == STYLE_BALL) {
            swapDisplayStyle(STYLE_EDGE)
        }
    }
    private val autoCollapseMs = 15_000L

    // 亮屏广播接收（屏幕亮/解锁时拉未读 + 强制 WS 重连）
    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                Intent.ACTION_SCREEN_ON,
                Intent.ACTION_USER_PRESENT -> {
                    // v0.8.13：Doze 里 WebSocket 可能静默断开 + scheduleReconnect 的 Thread.sleep 也会被暂停，
                    // 亮屏后强制 stop+start 一次，确保长连恢复。
                    runCatching { ws?.stop() }
                    startWebSocket()
                    pullUnreadFromServer()
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        prefs = getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        startAsForeground()
        addBubble()
        observeTasks()
        startForegroundDetectLoop()
        startWebSocket()
        registerReceiver(screenReceiver, IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_USER_PRESENT)
        })
        // 启动时先拉一次未读（避免错过未在线时下发的任务）
        pullUnreadFromServer()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        scope.cancel()
        ws?.stop()
        collapseHandler.removeCallbacksAndMessages(null)
        runCatching { unregisterReceiver(screenReceiver) }
        removePanel()
        removeBubble()
        urgentViews.toList().forEach { runCatching { wm.removeView(it) } }
        urgentViews.clear()
        runCatching { voiceRecognizer?.destroy() }
        voiceRecognizer = null
        super.onDestroy()
    }

    /** v0.8.10：横竖屏切换后重新吸边，避免悬浮窗跑到屏外 */
    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        val p = bubbleParams ?: return
        val root = bubbleView ?: return
        val screenW = resources.displayMetrics.widthPixels
        val screenH = resources.displayMetrics.heightPixels
        val w = if (root.width > 0) root.width else dp(56)
        // 保持"哪边就贴哪边"：按中心点判断
        p.x = if (p.x + w / 2 < screenW / 2) 0 else screenW - w
        // Y 不超屏
        val h = if (root.height > 0) root.height else dp(56)
        p.y = p.y.coerceIn(0, (screenH - h).coerceAtLeast(0))
        runCatching { wm.updateViewLayout(root, p) }
        updateBubbleUi(pendingCount)
    }

    /** v0.8.10：为一条新任务发系统通知（避免悬浮窗关/未授权时错过）
     *  v0.8.13：紧急消息用独立高优先级通道 + fullScreenIntent，锁屏/黑屏也能亮屏弹窗 */
    private fun notifyIncomingTask(
        taskId: Long, title: String, content: String,
        publisher: String, urgent: Boolean,
        serverId: Long = -1L, remindAt: Long = 0L,
    ) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            // 普通通道
            nm.createNotificationChannel(
                NotificationChannel(
                    INCOMING_CHANNEL_ID, "群消息 / 广播提醒",
                    NotificationManager.IMPORTANCE_DEFAULT
                )
            )
            // 紧急通道：高优先级 + 绕过勿扰 + 锁屏可见
            val urgentCh = NotificationChannel(
                INCOMING_URGENT_CHANNEL_ID, "紧急通知",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "🔴 紧急消息（黑屏/锁屏时会自动亮屏弹窗）"
                enableVibration(true)
                setBypassDnd(true)
                lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
            }
            nm.createNotificationChannel(urgentCh)
        }
        val channelId = if (urgent) INCOMING_URGENT_CHANNEL_ID else INCOMING_CHANNEL_ID
        val contentPi = PendingIntent.getActivity(
            this, taskId.toInt(),
            Intent(this, MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val prefix = if (urgent) "🔴 " else ""
        val ct = content.ifBlank { "$publisher 发来一条消息" }
        val builder = NotificationCompat.Builder(this, channelId)
            .setContentTitle("$prefix$title")
            .setContentText(ct)
            .setStyle(NotificationCompat.BigTextStyle().bigText(ct))
            .setSmallIcon(R.drawable.ic_bubble)
            .setContentIntent(contentPi)
            .setAutoCancel(true)
            .setPriority(if (urgent) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
        if (urgent) {
            // v0.8.13：fullScreenIntent 让黑屏/锁屏时 Android 自动亮屏并弹出 UrgentAlertActivity
            val fullIntent = Intent(this, com.example.noticefloat.ui.UrgentAlertActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                putExtra(com.example.noticefloat.ui.UrgentAlertActivity.EXTRA_LOCAL_ID, taskId)
                putExtra(com.example.noticefloat.ui.UrgentAlertActivity.EXTRA_SERVER_ID, serverId)
                putExtra(com.example.noticefloat.ui.UrgentAlertActivity.EXTRA_TITLE, title)
                putExtra(com.example.noticefloat.ui.UrgentAlertActivity.EXTRA_CONTENT, content)
                putExtra(com.example.noticefloat.ui.UrgentAlertActivity.EXTRA_PUBLISHER, publisher)
                putExtra(com.example.noticefloat.ui.UrgentAlertActivity.EXTRA_REMIND_AT, remindAt)
            }
            val fullPi = PendingIntent.getActivity(
                this, taskId.toInt() xor 0x55AA, fullIntent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
            builder.setFullScreenIntent(fullPi, true)
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
        }
        runCatching {
            nm.notify(NOTIF_TAG_INCOMING, (taskId and 0x7FFFFFFF).toInt(), builder.build())
        }
    }

    /** v0.8.10：标为已读后取消对应系统通知 */
    private fun dismissIncomingNotif(taskId: Long) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        runCatching {
            nm.cancel(NOTIF_TAG_INCOMING, (taskId and 0x7FFFFFFF).toInt())
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_RELOAD -> reloadBubble()
            ACTION_HIDE_QUICK_PANEL -> removeQuickPublishPanel()
        }
        return START_STICKY
    }

    // ============ 前台通知 ============
    private fun startAsForeground() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CHANNEL_ID, "NoticeFloat 常驻",
                NotificationManager.IMPORTANCE_LOW
            )
            nm.createNotificationChannel(ch)
        }
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val notif: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("NoticeFloat 正在运行")
            .setContentText("点击进入管理页")
            .setSmallIcon(R.drawable.ic_bubble)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
        startForeground(NOTIF_ID, notif)
    }

    // ============ 悬浮球 ============
    private fun baseStyle(): String =
        prefs.getString(KEY_BUBBLE_STYLE, STYLE_BALL) ?: STYLE_BALL

    private fun addBubble(displayStyle: String? = null, keepPos: Pair<Int, Int>? = null) {
        if (bubbleView != null) return
        val style = displayStyle ?: baseStyle()
        val layoutId = if (style == STYLE_EDGE) R.layout.view_floating_edge
                       else R.layout.view_floating_bubble
        val root = LayoutInflater.from(this).inflate(layoutId, null)
        bubbleView = root
        bubbleStyle = style

        val screenW = resources.displayMetrics.widthPixels
        bubbleParams = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            overlayType(),
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            if (keepPos != null) {
                x = keepPos.first
                y = keepPos.second
            } else {
                // edge 模式默认贴屏幕右侧；ball 模式默认屏幕左侧
                x = if (style == STYLE_EDGE) screenW else 0
                y = 400
            }
        }

        root.setOnTouchListener(BubbleDragListener {
            handleBubbleClick()
        })

        updateBubbleUi(pendingCount)
        try {
            wm.addView(bubbleView, bubbleParams)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun removeBubble() {
        bubbleView?.let { runCatching { wm.removeView(it) } }
        bubbleView = null
    }

    /** 供外部（MainActivity）切换样式后立刻生效 */
    fun reloadBubble() {
        collapseHandler.removeCallbacks(collapseRunnable)
        removeBubble()
        addBubble()
    }

    /** 悬浮球点击处理：
     * - 基准=EDGE、当前=EDGE：展开为圆球（不开面板），启动15s定时器
     * - 基准=EDGE、当前=BALL：打开面板，同时续期定时器
     * - 基准=BALL：直接打开面板，不启定时器
     */
    private fun handleBubbleClick() {
        val base = baseStyle()
        if (base == STYLE_EDGE && bubbleStyle == STYLE_EDGE) {
            swapDisplayStyle(STYLE_BALL)
            scheduleAutoCollapse()
        } else {
            togglePanel()
            if (base == STYLE_EDGE) scheduleAutoCollapse()
        }
    }

    private fun scheduleAutoCollapse() {
        collapseHandler.removeCallbacks(collapseRunnable)
        collapseHandler.postDelayed(collapseRunnable, autoCollapseMs)
    }

    /** 在不弄丢位置的前提下切换悬浮球形态 */
    private fun swapDisplayStyle(newStyle: String) {
        if (bubbleStyle == newStyle) return
        val p = bubbleParams
        val screenW = resources.displayMetrics.widthPixels
        // 从 EDGE 展开为 BALL：向屏内偏移 24dp，避免真的贴到屏外
        val keep: Pair<Int, Int>? = if (p != null) {
            val nx = when {
                newStyle == STYLE_BALL && p.x >= screenW - 4 -> screenW - dp(56)
                newStyle == STYLE_BALL && p.x <= 4 -> dp(8)
                newStyle == STYLE_EDGE && p.x > screenW / 2 -> screenW
                newStyle == STYLE_EDGE && p.x <= screenW / 2 -> 0
                else -> p.x
            }
            nx to p.y
        } else null
        removeBubble()
        addBubble(newStyle, keep)
    }

    private fun dp(v: Int): Int =
        (v * resources.displayMetrics.density).toInt()

    private fun updateBubbleUi(count: Int, topTitle: String = "") {
        val root = bubbleView ?: return
        pendingCount = count
        if (bubbleStyle == STYLE_EDGE) {
            val strip = root.findViewById<View>(R.id.edgeStrip)
            val badge = root.findViewById<TextView>(R.id.edgeBadge)
            val screenW = resources.displayMetrics.widthPixels
            val onLeft = (bubbleParams?.x ?: 0) < screenW / 2
            val bg = when {
                count == 0 && onLeft -> R.drawable.bg_bubble_edge_left
                count == 0          -> R.drawable.bg_bubble_edge
                onLeft              -> R.drawable.bg_bubble_edge_left_alert
                else                -> R.drawable.bg_bubble_edge_alert
            }
            strip.setBackgroundResource(bg)
            badge.text = when {
                count == 0 -> ""
                count > 99 -> "99+"
                else       -> count.toString()
            }
        } else {
            val dot = root.findViewById<ImageView>(R.id.dotIcon)
            val badge = root.findViewById<TextView>(R.id.badge)
            val keyword = root.findViewById<TextView>(R.id.keyword)
            if (count == 0) {
                badge.visibility = View.GONE
                keyword?.text = ""
                dot.setImageResource(R.drawable.bg_bubble_idle)
            } else {
                badge.visibility = if (count > 1) View.VISIBLE else View.GONE
                badge.text = if (count > 99) "99+" else count.toString()
                dot.setImageResource(R.drawable.bg_bubble_alert)
                keyword?.text = extractKeyword(topTitle)
            }
        }
    }

    /**
     * 从标题中提取"关键字"用于圆球中央展示。
     * 规则：优先匹配预设高辨识度词；否则取标题前 2 个字（中文）或前 3 个字符。
     */
    private fun extractKeyword(title: String): String {
        if (title.isBlank()) return ""
        val presets = listOf(
            "盐场", "十殿", "竞技", "蟠桃", "赛车", "副本", "限时",
            "福利", "签到", "boss", "BOSS", "抽奖", "活动", "开服",
            "维护", "更新", "首充", "月卡", "任务", "红包"
        )
        for (k in presets) if (title.contains(k, ignoreCase = true)) return k.take(2)
        // 去掉常见前缀符号
        val t = title.trimStart('【', '[', '（', '(', ' ').trim()
        // 中文优先取前 2 字
        val cn = t.filter { it in '\u4e00'..'\u9fa5' }
        return if (cn.length >= 2) cn.take(2) else t.take(3)
    }

    private fun setBubbleVisible(visible: Boolean) {
        bubbleView?.visibility = if (visible) View.VISIBLE else View.GONE
    }

    // ============ 展开面板 ============
    private fun togglePanel() {
        if (isPanelOpen) removePanel() else showPanel()
    }

    private fun showPanel() {
        if (panelView != null) return
        val binding = ViewFloatingPanelBinding.inflate(LayoutInflater.from(this))
        panelView = binding.root
        panelParams = WindowManager.LayoutParams(
            (resources.displayMetrics.widthPixels * 0.86).toInt(),
            WindowManager.LayoutParams.WRAP_CONTENT,
            overlayType(),
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                or WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 40
            y = (bubbleParams?.y ?: 300) + 120
        }

        val adapter = TaskAdapter(
            onClick = { task -> markDone(task) },
            onLongClick = { task -> deleteTask(task) }
        )
        binding.list.layoutManager = LinearLayoutManager(this)
        binding.list.adapter = adapter
        binding.btnClose.setOnClickListener { removePanel() }
        binding.btnPublish.setOnClickListener {
            startActivity(Intent(this, PublisherActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            removePanel()
        }
        binding.btnDoneAll.setOnClickListener {
            // 遍历当前展示的列表逐条完成
            adapter.currentList.forEach { markDone(it) }
        }

        scope.launch {
            (application as NoticeApp).repository.observePending().collect { list ->
                adapter.submitList(list)
                binding.empty.visibility = if (list.isEmpty()) View.VISIBLE else View.GONE
            }
        }

        try {
            wm.addView(panelView, panelParams)
            isPanelOpen = true
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun removePanel() {
        panelView?.let { runCatching { wm.removeView(it) } }
        panelView = null
        isPanelOpen = false
    }

    // ============ 紧急强弹窗（顶层，需手动确认） ============
    private val urgentViews = mutableListOf<View>()

    private fun showUrgentAlert(task: Task) {
        val inflater = LayoutInflater.from(this)
        val root = inflater.inflate(R.layout.view_urgent_alert, null)
        val tvTitle = root.findViewById<android.widget.TextView>(R.id.tvTitle)
        val tvContent = root.findViewById<android.widget.TextView>(R.id.tvContent)
        val tvMeta = root.findViewById<android.widget.TextView>(R.id.tvMeta)
        val btnAck = root.findViewById<android.widget.Button>(R.id.btnAck)
        val btnLater = root.findViewById<android.widget.Button>(R.id.btnLater)
        tvTitle.text = task.title
        // v0.8.8：正文为空时直接隐藏 tvContent，不再显示"（无正文）"
        if (task.content.isBlank()) {
            tvContent.visibility = View.GONE
        } else {
            tvContent.visibility = View.VISIBLE
            tvContent.text = task.content
        }
        val timeStr = if (task.remindAt > 0)
            android.text.format.DateFormat.format("MM-dd HH:mm", task.remindAt).toString()
        else "无提醒时间"
        tvMeta.text = "来自 ${task.publisher} · $timeStr"

        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        else
            @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE
        // v0.7：横幅式（贴顶部，不模态，不 dim，不遮挡桌面/微信）
        // v0.8.13：加 SHOW_WHEN_LOCKED 让锁屏亮起后横幅也能可见
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP
            // 距离顶部留 24dp，避免被状态栏挤压
            y = (24 * resources.displayMetrics.density).toInt()
        }

        val remove: () -> Unit = {
            runCatching { wm.removeView(root) }
            urgentViews.remove(root)
        }
        btnAck.setOnClickListener {
            remove()
            markDone(task)
        }
        btnLater.setOnClickListener { remove() }

        runCatching { wm.addView(root, lp) }.onSuccess { urgentViews.add(root) }
    }

    private fun markDone(task: Task) {
        // v0.8.7：升级消息不算"完成"，改为跳转 MainActivity 触发下载安装
        if (task.publisher == "__update__") {
            val relUrl = task.groupCode ?: ""
            val intent = Intent(this, com.example.noticefloat.ui.MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                putExtra("trigger_update", true)
                putExtra("upgrade_url", relUrl)
            }
            startActivity(intent)
            removePanel()
            return
        }
        scope.launch(Dispatchers.IO) {
            val repo = (application as NoticeApp).repository
            repo.markDone(task.id)
            // v0.8.10：已读同步取消系统通知
            withContext(Dispatchers.Main) { dismissIncomingNotif(task.id) }
            // 群任务同步到服务端
            val sid = task.serverId
            if (task.source == "group" && sid != null) {
                try {
                    com.example.noticefloat.remote.ApiClient(
                        Session.get(this@FloatingService)
                    ).markDone(sid)
                } catch (_: Exception) { /* 离线时后续可加重试队列 */ }
            }
        }
    }

    private fun deleteTask(task: Task) {
        scope.launch(Dispatchers.IO) {
            (application as NoticeApp).repository.delete(task.id)
        }
    }

    // ============ 数据订阅 ============
    private fun observeTasks() {
        scope.launch {
            (application as NoticeApp).repository.observePending().collect { list ->
                val topTitle = list.firstOrNull()?.title ?: ""
                updateBubbleUi(list.size, topTitle)
            }
        }
    }

    // 悬浮球永远显示（用户明确要求）——保留旧循环结构但直接确保 visible
    private fun startForegroundDetectLoop() {
        detectJob?.cancel()
        detectJob = scope.launch {
            setBubbleVisible(true)
        }
    }

    // ============ WebSocket 长连 ============
    private fun startWebSocket() {
        val session = Session.get(this)
        if (!session.isConfigured()) return
        ws = WsClient(
            session = session,
            onEvent = { type, obj ->
                when (type) {
                    "task.new" -> handleRemoteTaskNew(obj.optJSONObject("task"))
                    "task.done" -> handleRemoteTaskDone(
                        obj.optLong("task_id", -1),
                        obj.optString("by_name")
                    )
                }
            },
            onStatus = { connected ->
                // 每次 WS 重连成功 → 拉一次未读，补偿断网期间遗漏的任务
                if (connected) pullUnreadFromServer()
            }
        ).also { it.start() }
    }

    private fun handleRemoteTaskNew(taskObj: org.json.JSONObject?) {
        taskObj ?: return
        val serverId = taskObj.optLong("id", -1L)
        if (serverId <= 0) return
        val title = taskObj.optString("title")
        val content = taskObj.optString("content")
        val remindAt = taskObj.optLong("remind_at", 0L)
        val publisherName = taskObj.optString("publisher_name", "群成员")
        val groupCode = taskObj.optString("group_code")
        val urgent = taskObj.optBoolean("urgent", false)
        scope.launch(Dispatchers.IO) {
            val repo = (application as NoticeApp).repository
            val local = Task(
                title = title,
                content = content,
                remindAt = remindAt,
                publisher = publisherName,
                source = "group",
                groupCode = groupCode,
                serverId = serverId,
                urgent = urgent
            )
            val id = repo.upsertFromServer(local)
            if (id > 0 && remindAt > 0) {
                com.example.noticefloat.reminder.ReminderScheduler.schedule(
                    this@FloatingService, id, remindAt
                )
            }
            if (id > 0) withContext(Dispatchers.Main) {
                // v0.8.10：无论悬浮窗是否开，都发一条系统通知，已读时同步取消；
                // 能保证未授权悬浮窗 / 悬浮窗隐藏 / 屏幕锁定时也能看到
                // v0.8.13：紧急消息带 fullScreenIntent + serverId，黑屏能亮屏、已知晓能同步服务端
                notifyIncomingTask(id, title, content, publisherName, urgent, serverId, remindAt)
            }
            if (id > 0 && urgent) withContext(Dispatchers.Main) {
                showUrgentAlert(local.copy(id = id))
            }
        }
    }

    private fun handleRemoteTaskDone(serverId: Long, byName: String) {
        if (serverId <= 0) return
        scope.launch(Dispatchers.IO) {
            val repo = (application as NoticeApp).repository
            val existing = repo.getByServerId(serverId) ?: return@launch
            if (existing.status != 1) repo.markDone(existing.id)
            // v0.8.10：其他设备标为完成，本机也取消对应系统通知
            withContext(Dispatchers.Main) { dismissIncomingNotif(existing.id) }
        }
    }

    /** 亮屏/解锁/WS 重连时拉未读任务 */
    private fun pullUnreadFromServer() {
        val session = Session.get(this)
        if (!session.isConfigured()) return
        scope.launch(Dispatchers.IO) {
            runCatching {
                val since = prefs.getLong(KEY_LAST_SYNC_MS, 0L)
                val arr = com.example.noticefloat.remote.ApiClient(session).pullUnread(since)
                var maxCreated = since
                for (i in 0 until arr.length()) {
                    val obj = arr.getJSONObject(i)
                    handleRemoteTaskNew(obj)
                    val ct = obj.optLong("created_at", 0L)
                    if (ct > maxCreated) maxCreated = ct
                }
                if (maxCreated > since) {
                    prefs.edit().putLong(KEY_LAST_SYNC_MS, maxCreated).apply()
                }
            }
        }
    }

    private fun overlayType(): Int {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }
    }

    /** 悬浮球拖拽监听（v0.8 加长按触发语音输入） */
    private inner class BubbleDragListener(val onClick: () -> Unit) : View.OnTouchListener {
        private var initialX = 0
        private var initialY = 0
        private var initialTouchX = 0f
        private var initialTouchY = 0f
        private var isDragging = false
        private var isLongPressed = false
        private val longPressHandler = Handler(Looper.getMainLooper())
        private val longPressRunnable = Runnable {
            if (!isDragging) {
                isLongPressed = true
                triggerVoiceInput()
            }
        }

        override fun onTouch(v: View, event: MotionEvent): Boolean {
            val params = bubbleParams ?: return false
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    initialX = params.x
                    initialY = params.y
                    initialTouchX = event.rawX
                    initialTouchY = event.rawY
                    isDragging = false
                    isLongPressed = false
                    longPressHandler.postDelayed(longPressRunnable, 550L)
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - initialTouchX
                    val dy = event.rawY - initialTouchY
                    if (!isDragging && (abs(dx) > 12 || abs(dy) > 12)) {
                        isDragging = true
                        longPressHandler.removeCallbacks(longPressRunnable)
                    }
                    if (isDragging) {
                        params.x = (initialX + dx).toInt()
                        params.y = (initialY + dy).toInt()
                        runCatching { wm.updateViewLayout(bubbleView, params) }
                    }
                }
                MotionEvent.ACTION_UP,
                MotionEvent.ACTION_CANCEL -> {
                    longPressHandler.removeCallbacks(longPressRunnable)
                    if (isLongPressed) {
                        // 语音: 松手结束录音
                        stopVoiceCapture()
                    } else if (!isDragging) {
                        onClick()
                    } else {
                        // 吸边：靠近哪边就贴哪边
                        val screenW = resources.displayMetrics.widthPixels
                        val bubbleW = v.width
                        params.x = if (params.x + bubbleW / 2 < screenW / 2) 0
                                   else screenW - bubbleW
                        runCatching { wm.updateViewLayout(bubbleView, params) }
                        updateBubbleUi(pendingCount)
                        // v0.8.6：拖到边缘就自动切换成边缘悬浮条
                        if (bubbleStyle == STYLE_BALL) {
                            swapDisplayStyle(STYLE_EDGE)
                        }
                    }
                }
            }
            return true
        }
    }

    // ============ v0.8.2 语音输入（长按开始，松手结束） ============
    private fun startVoiceCapture() {
        // 权限检查
        if (androidx.core.content.ContextCompat.checkSelfPermission(
                this, android.Manifest.permission.RECORD_AUDIO
            ) != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            android.widget.Toast.makeText(this, "首次使用需授予录音权限", android.widget.Toast.LENGTH_SHORT).show()
            try {
                startActivity(
                    Intent(this, com.example.noticefloat.ui.VoicePermissionActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            } catch (_: Exception) {}
            return
        }
        if (!android.speech.SpeechRecognizer.isRecognitionAvailable(this)) {
            android.widget.Toast.makeText(this, "系统无可用语音识别引擎", android.widget.Toast.LENGTH_LONG).show()
            return
        }
        if (voiceListening) return
        try {
            voiceGotResult = false
            voiceRecognizer?.destroy()
            voiceRecognizer = android.speech.SpeechRecognizer.createSpeechRecognizer(this).apply {
                setRecognitionListener(object : android.speech.RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) {}
                    override fun onBeginningOfSpeech() {}
                    override fun onRmsChanged(rmsdB: Float) {}
                    override fun onBufferReceived(buffer: ByteArray?) {}
                    override fun onEndOfSpeech() {}
                    override fun onError(error: Int) {
                        voiceListening = false
                        stopBubbleTalkAnim()
                        if (voiceGotResult) return
                        android.util.Log.w("Voice", "onError code=$error offlineTried=$voiceOnlineFallbackTried langTried=$voiceFallbackTried")
                        // v0.8.6：code=12 = ERROR_LANGUAGE_UNAVAILABLE，去掉 EXTRA_LANGUAGE 兜底重试一次
                        if (error == 12 && !voiceFallbackTried) {
                            voiceFallbackTried = true
                            android.widget.Toast.makeText(this@FloatingService, "🎙 切换到系统默认语言重试", android.widget.Toast.LENGTH_SHORT).show()
                            startVoiceCapture()
                            return
                        }
                        // v0.8.12：NO_MATCH / SPEECH_TIMEOUT / SERVER / CLIENT 且当前尝试是 offline → 切在线再试一次
                        val retryable = error == android.speech.SpeechRecognizer.ERROR_NO_MATCH
                                || error == android.speech.SpeechRecognizer.ERROR_SPEECH_TIMEOUT
                                || error == android.speech.SpeechRecognizer.ERROR_SERVER
                                || error == android.speech.SpeechRecognizer.ERROR_CLIENT
                        if (retryable && !voiceOnlineFallbackTried) {
                            voiceOnlineFallbackTried = true
                            android.widget.Toast.makeText(this@FloatingService, "🎙 离线未识别，切在线重试", android.widget.Toast.LENGTH_SHORT).show()
                            startVoiceCapture()
                            return
                        }
                        val msg = when (error) {
                            android.speech.SpeechRecognizer.ERROR_NO_MATCH -> "没听清，请重试"
                            android.speech.SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "没检测到语音"
                            android.speech.SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "缺少录音权限"
                            android.speech.SpeechRecognizer.ERROR_NETWORK,
                            android.speech.SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
                                "网络异常（系统 ASR 需联网/GMS，本机不可用）"
                            android.speech.SpeechRecognizer.ERROR_CLIENT -> "识别服务出错"
                            android.speech.SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "识别引擎繁忙"
                            android.speech.SpeechRecognizer.ERROR_SERVER -> "识别服务不可用"
                            11 -> "语音引擎太老，请系统设置更新"
                            12 -> "手机不支持中文识别，请设置里下载语言包"
                            else -> "识别失败 (code=$error)"
                        }
                        android.widget.Toast.makeText(this@FloatingService, "🎙 $msg", android.widget.Toast.LENGTH_LONG).show()
                        // 网络类错误 → 兜底跳发布页让用户用键盘话筒
                        if (error == android.speech.SpeechRecognizer.ERROR_NETWORK ||
                            error == android.speech.SpeechRecognizer.ERROR_NETWORK_TIMEOUT ||
                            error == android.speech.SpeechRecognizer.ERROR_SERVER
                        ) {
                            openPublisherWithText("")
                        }
                    }
                    override fun onResults(results: Bundle?) {
                        voiceListening = false
                        stopBubbleTalkAnim()
                        val list = results?.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION)
                        val text = list?.firstOrNull().orEmpty().trim()
                        if (text.isNotBlank()) {
                            voiceGotResult = true
                            if (voiceIntoQuickPanel) {
                                // 注入到快捷发布小窗
                                injectVoiceToQuickPanel(text)
                            } else {
                                android.widget.Toast.makeText(this@FloatingService, "🎙 $text", android.widget.Toast.LENGTH_SHORT).show()
                                openPublisherWithText(text)
                            }
                        } else {
                            android.widget.Toast.makeText(this@FloatingService, "🎙 未识别到内容", android.widget.Toast.LENGTH_SHORT).show()
                        }
                    }
                    override fun onPartialResults(partial: Bundle?) {}
                    override fun onEvent(eventType: Int, params: Bundle?) {}
                })
            }
            val intent = Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                    android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                if (!voiceFallbackTried) {
                    putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                    putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "zh-CN")
                } // fallback：不指定语言，交给系统默认
                // v0.8.12：提高 MAX_RESULTS，宽松静音判定，尽量兜住短句/慢语速
                putExtra(android.speech.RecognizerIntent.EXTRA_MAX_RESULTS, 3)
                putExtra(android.speech.RecognizerIntent.EXTRA_CALLING_PACKAGE, packageName)
                putExtra(android.speech.RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 2000L)
                putExtra(android.speech.RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 2000L)
                putExtra(android.speech.RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 1500L)
                // v0.8.12：先离线，NO_MATCH/SPEECH_TIMEOUT 兜底会自动切在线
                putExtra(android.speech.RecognizerIntent.EXTRA_PREFER_OFFLINE, !voiceOnlineFallbackTried)
            }
            voiceRecognizer?.startListening(intent)
            voiceListening = true
            startBubbleTalkAnim()
            android.widget.Toast.makeText(this, "🎙 聆听中…松开结束", android.widget.Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            e.printStackTrace()
            voiceListening = false
            android.widget.Toast.makeText(this, "启动语音识别失败", android.widget.Toast.LENGTH_SHORT).show()
        }
    }

    private fun stopVoiceCapture() {
        if (!voiceListening) return
        try {
            voiceRecognizer?.stopListening()
        } catch (_: Exception) {}
    }

    private fun openPublisherWithText(text: String) {
        try {
            val i = Intent(this, PublisherActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (text.isNotBlank()) {
                i.putExtra(PublisherActivity.EXTRA_PREFILL_CONTENT, text)
            }
            startActivity(i)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun triggerVoiceInput() {
        // v0.8.7：长按 → 弹快捷发布小窗 + 同时启动语音识别监听
        showQuickPublishPanel()
        voiceIntoQuickPanel = true
        voiceFallbackTried = false
        voiceOnlineFallbackTried = false
        startVoiceCapture()
    }

    // ============ v0.8.6 快捷发布悬浮小窗 ============
    private var quickPublishView: View? = null
    private var quickPublishParams: WindowManager.LayoutParams? = null
    private var quickEtContent: android.widget.EditText? = null
    private var quickTvHint: android.widget.TextView? = null

    private fun showQuickPublishPanel() {
        if (quickPublishView != null) {
            // 已打开则关闭
            removeQuickPublishPanel(); return
        }
        removePanel() // 收起待办面板
        val v = LayoutInflater.from(this).inflate(R.layout.view_floating_publish, null)
        quickPublishView = v
        quickEtContent = v.findViewById(R.id.etContent)
        quickTvHint = v.findViewById(R.id.tvHint)
        val cbUrgent = v.findViewById<android.widget.CheckBox>(R.id.cbUrgent)
        val btnClose = v.findViewById<android.widget.Button>(R.id.btnClose)
        val btnSend = v.findViewById<android.widget.Button>(R.id.btnSend)

        quickPublishParams = WindowManager.LayoutParams(
            (resources.displayMetrics.widthPixels * 0.90).toInt(),
            WindowManager.LayoutParams.WRAP_CONTENT,
            overlayType(),
            // v0.8.8：TYPE_APPLICATION_OVERLAY 悬浮窗要能拉起系统输入法：
            //   flags 里 **不能**带 FLAG_NOT_FOCUSABLE，且窗口本身需 FOCUSABLE，
            //   否则点击 EditText 键盘不弹。ALT_FOCUSABLE_IM 保留以便 IME 出现时 dim 层友好。
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 20
            y = (bubbleParams?.y ?: 300) + 120
            softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE or
                WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE
        }

        // 根据本地缓存 role 显示提示
        val role = Session.get(this).role
        quickTvHint?.text = when (role) {
            "super" -> "🛡️ 超级管理员：发送即广播给所有设备"
            "admin" -> "🛡️ 管理员：发送即广播给所有设备"
            else -> "普通用户：发送仅在本机提醒（不推送他人）"
        }

        // v0.8.8：点击编辑框显式请求焦点 + 拉起输入法（悬浮窗上仅靠焦点触发不够可靠）
        val imm = getSystemService(android.content.Context.INPUT_METHOD_SERVICE)
            as android.view.inputmethod.InputMethodManager
        quickEtContent?.isFocusableInTouchMode = true
        quickEtContent?.setOnClickListener {
            quickEtContent?.requestFocus()
            imm.showSoftInput(quickEtContent, android.view.inputmethod.InputMethodManager.SHOW_FORCED)
        }
        quickEtContent?.setOnFocusChangeListener { view, hasFocus ->
            if (hasFocus) imm.showSoftInput(view, android.view.inputmethod.InputMethodManager.SHOW_FORCED)
        }

        btnClose.setOnClickListener { removeQuickPublishPanel() }
        btnSend.setOnClickListener {
            val content = quickEtContent?.text?.toString()?.trim().orEmpty()
            val urgent = cbUrgent.isChecked
            if (content.isBlank()) {
                android.widget.Toast.makeText(this, "请输入内容", android.widget.Toast.LENGTH_SHORT).show(); return@setOnClickListener
            }
            // 标题自动取内容前 20 字
            val title = content.take(20)
            sendQuickPublish(title, content, urgent, role)
        }

        try {
            wm.addView(v, quickPublishParams)
        } catch (e: Exception) { e.printStackTrace() }
    }

    private fun removeQuickPublishPanel() {
        quickPublishView?.let { runCatching { wm.removeView(it) } }
        quickPublishView = null
        quickEtContent = null
        quickTvHint = null
        voiceIntoQuickPanel = false
    }

    private fun injectVoiceToQuickPanel(text: String) {
        val et = quickEtContent ?: return
        val old = et.text?.toString().orEmpty()
        val merged = if (old.isBlank()) text else "$old $text"
        et.setText(merged)
        et.setSelection(merged.length)
    }

    // ============ v0.8.7 圆球说话脉冲动效 ============
    private var bubbleTalkAnim: android.animation.ObjectAnimator? = null
    private fun startBubbleTalkAnim() {
        val root = bubbleView ?: return
        val dot = root.findViewById<android.view.View>(R.id.dotIcon) ?: return
        stopBubbleTalkAnim()
        bubbleTalkAnim = android.animation.ObjectAnimator.ofPropertyValuesHolder(
            dot,
            android.animation.PropertyValuesHolder.ofFloat("scaleX", 1f, 1.25f),
            android.animation.PropertyValuesHolder.ofFloat("scaleY", 1f, 1.25f),
            android.animation.PropertyValuesHolder.ofFloat("alpha", 1f, 0.55f)
        ).apply {
            duration = 480
            repeatMode = android.animation.ObjectAnimator.REVERSE
            repeatCount = android.animation.ObjectAnimator.INFINITE
            start()
        }
    }
    private fun stopBubbleTalkAnim() {
        bubbleTalkAnim?.cancel(); bubbleTalkAnim = null
        val root = bubbleView ?: return
        val dot = root.findViewById<android.view.View>(R.id.dotIcon) ?: return
        dot.scaleX = 1f; dot.scaleY = 1f; dot.alpha = 1f
    }

    private fun sendQuickPublish(title: String, content: String, urgent: Boolean, role: String) {
        val isBroadcaster = (role == "super" || role == "admin")
        scope.launch {
            try {
                if (isBroadcaster) {
                    // 走后端广播
                    withContext(Dispatchers.IO) {
                        val api = com.example.noticefloat.remote.ApiClient(Session.get(this@FloatingService))
                        api.publishBroadcast(title, content, 0L, urgent)
                    }
                    android.widget.Toast.makeText(this@FloatingService, "📢 已广播", android.widget.Toast.LENGTH_SHORT).show()
                } else {
                    // 普通用户：本地插入 Repository（走本地闹钟）
                    val repo = (application as NoticeApp).repository
                    withContext(Dispatchers.IO) {
                        repo.add(Task(
                            title = title,
                            content = content,
                            remindAt = 0L,
                            urgent = urgent,
                            source = "local",
                            publisher = Session.get(this@FloatingService).nickname,
                        ))
                    }
                    android.widget.Toast.makeText(this@FloatingService, "✔ 已加入本机待办", android.widget.Toast.LENGTH_SHORT).show()
                }
                removeQuickPublishPanel()
            } catch (e: Exception) {
                e.printStackTrace()
                android.widget.Toast.makeText(this@FloatingService, "发送失败：${e.message}", android.widget.Toast.LENGTH_LONG).show()
            }
        }
    }
}

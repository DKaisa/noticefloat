package com.example.noticefloat.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
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
        const val PREF_NAME = "notice_float_prefs"
        const val KEY_ONLY_WHEN_WECHAT = "only_when_wechat"
        const val KEY_BUBBLE_STYLE = "bubble_style"
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

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var detectJob: Job? = null
    private var ws: WsClient? = null

    override fun onCreate() {
        super.onCreate()
        wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        prefs = getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        startAsForeground()
        addBubble()
        observeTasks()
        startForegroundDetectLoop()
        startWebSocket()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        scope.cancel()
        ws?.stop()
        removePanel()
        removeBubble()
        urgentViews.toList().forEach { runCatching { wm.removeView(it) } }
        urgentViews.clear()
        super.onDestroy()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_RELOAD) {
            reloadBubble()
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
    private fun addBubble() {
        if (bubbleView != null) return
        val style = prefs.getString(KEY_BUBBLE_STYLE, STYLE_BALL) ?: STYLE_BALL
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
            // edge 模式默认贴屏幕右侧；ball 模式默认屏幕左侧
            x = if (style == STYLE_EDGE) screenW else 0
            y = 400
        }

        root.setOnTouchListener(BubbleDragListener {
            togglePanel()
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
        removeBubble()
        addBubble()
    }

    private fun updateBubbleUi(count: Int) {
        val root = bubbleView ?: return
        pendingCount = count
        if (bubbleStyle == STYLE_EDGE) {
            val strip = root.findViewById<View>(R.id.edgeStrip)
            val badge = root.findViewById<TextView>(R.id.edgeBadge)
            if (count == 0) {
                strip.setBackgroundResource(R.drawable.bg_bubble_edge)
                badge.text = ""
            } else {
                strip.setBackgroundResource(R.drawable.bg_bubble_edge_alert)
                badge.text = if (count > 99) "99+" else count.toString()
            }
        } else {
            val dot = root.findViewById<ImageView>(R.id.dotIcon)
            val badge = root.findViewById<TextView>(R.id.badge)
            if (count == 0) {
                badge.visibility = View.GONE
                dot.setImageResource(R.drawable.bg_bubble_idle)
            } else {
                badge.visibility = View.VISIBLE
                badge.text = if (count > 99) "99+" else count.toString()
                dot.setImageResource(R.drawable.bg_bubble_alert)
            }
        }
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
        tvContent.text = if (task.content.isBlank()) "（无正文）" else task.content
        val timeStr = if (task.remindAt > 0)
            android.text.format.DateFormat.format("MM-dd HH:mm", task.remindAt).toString()
        else "无提醒时间"
        tvMeta.text = "来自 ${task.publisher} · $timeStr"

        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        else
            @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            type,
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                    WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_DIM_BEHIND,
            PixelFormat.TRANSLUCENT
        ).apply {
            dimAmount = 0.35f
            gravity = Gravity.CENTER
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
        scope.launch(Dispatchers.IO) {
            val repo = (application as NoticeApp).repository
            repo.markDone(task.id)
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
            (application as NoticeApp).repository.observePendingCount().collect { count ->
                updateBubbleUi(count)
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

    /** 悬浮球拖拽监听 */
    private inner class BubbleDragListener(val onClick: () -> Unit) : View.OnTouchListener {
        private var initialX = 0
        private var initialY = 0
        private var initialTouchX = 0f
        private var initialTouchY = 0f
        private var isDragging = false

        override fun onTouch(v: View, event: MotionEvent): Boolean {
            val params = bubbleParams ?: return false
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    initialX = params.x
                    initialY = params.y
                    initialTouchX = event.rawX
                    initialTouchY = event.rawY
                    isDragging = false
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - initialTouchX
                    val dy = event.rawY - initialTouchY
                    if (!isDragging && (abs(dx) > 12 || abs(dy) > 12)) isDragging = true
                    if (isDragging) {
                        params.x = (initialX + dx).toInt()
                        params.y = (initialY + dy).toInt()
                        runCatching { wm.updateViewLayout(bubbleView, params) }
                    }
                }
                MotionEvent.ACTION_UP -> {
                    if (!isDragging) {
                        onClick()
                    } else {
                        // 吸边：靠近哪边就贴哪边
                        val screenW = resources.displayMetrics.widthPixels
                        val bubbleW = v.width
                        params.x = if (params.x + bubbleW / 2 < screenW / 2) 0
                                   else screenW - bubbleW
                        runCatching { wm.updateViewLayout(bubbleView, params) }
                    }
                }
            }
            return true
        }
    }
}

package com.example.noticefloat.ui

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.widget.Button
import android.widget.TextView
import com.example.noticefloat.NoticeApp
import com.example.noticefloat.R
import com.example.noticefloat.remote.ApiClient
import com.example.noticefloat.remote.Session
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * v0.8.13：紧急消息全屏弹窗 Activity。
 * 由 FloatingService.notifyIncomingTask() 的 fullScreenIntent 唤起：
 * - 黑屏 → 自动亮屏（turnScreenOn）
 * - 锁屏 → 直接显示（showWhenLocked，不需解锁）
 * 已知晓：本地标 done 并调服务端 /api/tasks/{id}/done。
 */
class UrgentAlertActivity : Activity() {

    companion object {
        const val EXTRA_LOCAL_ID = "local_id"
        const val EXTRA_SERVER_ID = "server_id"
        const val EXTRA_TITLE = "title"
        const val EXTRA_CONTENT = "content"
        const val EXTRA_PUBLISHER = "publisher"
        const val EXTRA_REMIND_AT = "remind_at"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // API 27+：Activity 属性亮屏；旧机型用 window flag 兜底
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
            (getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager)
                ?.requestDismissKeyguard(this, null)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                    WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            )
        }
        setContentView(R.layout.activity_urgent_alert)

        val title = intent.getStringExtra(EXTRA_TITLE).orEmpty()
        val content = intent.getStringExtra(EXTRA_CONTENT).orEmpty()
        val publisher = intent.getStringExtra(EXTRA_PUBLISHER) ?: "群成员"
        val remindAt = intent.getLongExtra(EXTRA_REMIND_AT, 0L)
        val localId = intent.getLongExtra(EXTRA_LOCAL_ID, -1L)
        val serverId = intent.getLongExtra(EXTRA_SERVER_ID, -1L)

        findViewById<TextView>(R.id.tvTitle).text = title.ifBlank { "(无标题)" }
        val tvContent = findViewById<TextView>(R.id.tvContent)
        if (content.isBlank()) tvContent.visibility = android.view.View.GONE
        else tvContent.text = content
        val timeStr = if (remindAt > 0)
            android.text.format.DateFormat.format("MM-dd HH:mm", remindAt).toString()
        else "无提醒时间"
        findViewById<TextView>(R.id.tvMeta).text = "来自 $publisher · $timeStr"

        findViewById<Button>(R.id.btnLater).setOnClickListener { finish() }
        findViewById<Button>(R.id.btnAck).setOnClickListener {
            ackTask(localId, serverId)
            finish()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        recreate()
    }

    private fun ackTask(localId: Long, serverId: Long) {
        val app = application as NoticeApp
        CoroutineScope(Dispatchers.IO).launch {
            runCatching {
                if (localId > 0) app.repository.markDone(localId)
                if (serverId > 0) {
                    val s = Session.get(this@UrgentAlertActivity)
                    if (s.isConfigured()) ApiClient(s).markDone(serverId)
                }
            }
        }
    }
}

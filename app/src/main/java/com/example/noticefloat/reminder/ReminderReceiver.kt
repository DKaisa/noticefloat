package com.example.noticefloat.reminder

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.example.noticefloat.NoticeApp
import com.example.noticefloat.R
import com.example.noticefloat.ui.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class ReminderReceiver : BroadcastReceiver() {
    companion object {
        const val CHANNEL_ID = "notice_float_reminder"
    }

    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getLongExtra(ReminderScheduler.EXTRA_TASK_ID, -1L)
        if (id <= 0) return
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val repo = (context.applicationContext as NoticeApp).repository
                val task = repo.getById(id) ?: return@launch
                if (task.status != 0) return@launch
                showNotification(context, id, task.title, task.content)
            } finally {
                pending.finish()
            }
        }
    }

    private fun showNotification(context: Context, id: Long, title: String, content: String) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID, "任务提醒", NotificationManager.IMPORTANCE_HIGH
                )
            )
        }
        val pi = PendingIntent.getActivity(
            context, id.toInt(),
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val notif = NotificationCompat.Builder(context, CHANNEL_ID)
            .setContentTitle("⏰ $title")
            .setContentText(content.ifBlank { "任务到期提醒" })
            .setSmallIcon(R.drawable.ic_bubble)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        nm.notify((id and 0x7FFFFFFF).toInt(), notif)
    }
}

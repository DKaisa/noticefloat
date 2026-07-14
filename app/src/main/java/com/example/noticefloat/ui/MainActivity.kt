package com.example.noticefloat.ui

import android.app.AlarmManager
import android.app.AppOpsManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.example.noticefloat.databinding.ActivityMainBinding
import com.example.noticefloat.service.FloatingService

class MainActivity : AppCompatActivity() {

    private lateinit var b: ActivityMainBinding

    private val requestNotifPerm = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* no-op */ refresh() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.btnOverlay.setOnClickListener { requestOverlay() }
        b.btnUsage.setOnClickListener { requestUsageAccess() }
        b.btnNotif.setOnClickListener { requestNotif() }
        b.btnAlarm.setOnClickListener { requestExactAlarm() }
        b.btnStart.setOnClickListener {
            FloatingService.start(this)
            Toast.makeText(this, "悬浮窗服务已启动", Toast.LENGTH_SHORT).show()
            refresh()
        }
        b.btnStop.setOnClickListener {
            FloatingService.stop(this)
            Toast.makeText(this, "已停止", Toast.LENGTH_SHORT).show()
            refresh()
        }
        b.btnPublish.setOnClickListener {
            startActivity(Intent(this, PublisherActivity::class.java))
        }
        b.btnGroups.setOnClickListener {
            startActivity(Intent(this, GroupsActivity::class.java))
        }
        b.btnSettings.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        b.switchOnlyWeChat.setOnCheckedChangeListener { _, checked ->
            getSharedPreferences(FloatingService.PREF_NAME, Context.MODE_PRIVATE)
                .edit().putBoolean(FloatingService.KEY_ONLY_WHEN_WECHAT, checked).apply()
        }

        // 悬浮样式：ball / edge
        val prefs = getSharedPreferences(FloatingService.PREF_NAME, Context.MODE_PRIVATE)
        val curStyle = prefs.getString(FloatingService.KEY_BUBBLE_STYLE, FloatingService.STYLE_BALL)
        if (curStyle == FloatingService.STYLE_EDGE) b.rbEdge.isChecked = true else b.rbBall.isChecked = true
        b.rgBubbleStyle.setOnCheckedChangeListener { _, id ->
            val style = if (id == b.rbEdge.id) FloatingService.STYLE_EDGE else FloatingService.STYLE_BALL
            prefs.edit().putString(FloatingService.KEY_BUBBLE_STYLE, style).apply()
            // 让服务立即重建悬浮球（若正在运行）
            FloatingService.reload(this)
        }
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun refresh() {
        val prefs = getSharedPreferences(FloatingService.PREF_NAME, Context.MODE_PRIVATE)
        b.switchOnlyWeChat.isChecked =
            prefs.getBoolean(FloatingService.KEY_ONLY_WHEN_WECHAT, false)

        b.statusOverlay.text = "悬浮窗权限：" + granted(Settings.canDrawOverlays(this))
        b.statusUsage.text = "微信前台检测(UsageStats)：" + granted(hasUsageAccess())
        b.statusNotif.text = "通知权限：" + granted(hasNotifPerm())
        b.statusAlarm.text = "精确闹钟：" + granted(hasExactAlarm())
    }

    private fun granted(b: Boolean) = if (b) "✅ 已授予" else "❌ 未授予"

    private fun requestOverlay() {
        if (!Settings.canDrawOverlays(this)) {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName")))
        }
    }

    private fun requestUsageAccess() {
        if (!hasUsageAccess()) {
            startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
        }
    }

    private fun requestNotif() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !hasNotifPerm()) {
            requestNotifPerm.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun requestExactAlarm() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !hasExactAlarm()) {
            startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                Uri.parse("package:$packageName")))
        }
    }

    private fun hasUsageAccess(): Boolean {
        val appOps = getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            appOps.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(), packageName)
        } else {
            @Suppress("DEPRECATION")
            appOps.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(), packageName)
        }
        return mode == AppOpsManager.MODE_ALLOWED
    }

    private fun hasNotifPerm(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED
        } else true
    }

    private fun hasExactAlarm(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val am = getSystemService(Context.ALARM_SERVICE) as AlarmManager
            am.canScheduleExactAlarms()
        } else true
    }
}

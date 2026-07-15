package com.example.noticefloat.ui

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.example.noticefloat.databinding.ActivitySettingsBinding
import com.example.noticefloat.remote.ApiClient
import com.example.noticefloat.remote.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class SettingsActivity : AppCompatActivity() {
    private lateinit var b: ActivitySettingsBinding
    private lateinit var session: Session

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(b.root)
        session = Session.get(this)
        // v0.8.5 服务器地址内置为只读展示
        b.etServer.setText(session.serverUrl)
        b.etServer.isEnabled = false
        b.etServer.isFocusable = false
        b.etNick.setText(session.nickname)
        b.tvDeviceId.text = "设备 ID: ${session.deviceId}"

        b.btnSave.setOnClickListener {
            val nick = b.etNick.text?.toString()?.trim().orEmpty()
            if (nick.isBlank()) {
                Toast.makeText(this, "昵称不能为空", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            session.nickname = nick
            Toast.makeText(this, "已保存（重启悬浮窗服务生效）", Toast.LENGTH_LONG).show()
            finish()
        }

        b.btnTest.setOnClickListener {
            lifecycleScope.launch {
                val ok = withContext(Dispatchers.IO) {
                    runCatching { ApiClient(session).health() }.getOrDefault(false)
                }
                Toast.makeText(this@SettingsActivity,
                    if (ok) "✅ 连接成功" else "❌ 无法连接",
                    Toast.LENGTH_LONG).show()
            }
        }

        // 查角色显示
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) {
                runCatching { ApiClient(session).getRole() }.getOrNull()
            }
            val roleStr = r?.optString("role") ?: "user"
            val desc = when (roleStr) {
                "super" -> "🛡️ 超级管理员（硬编码）"
                "admin" -> "🔑 管理员"
                else -> "👤 普通用户"
            }
            b.tvDeviceId.text = "设备 ID: ${session.deviceId}\n角色: $desc"
        }
    }
}

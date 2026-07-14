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
        b.etServer.setText(session.serverUrl)
        b.etNick.setText(session.nickname)
        b.tvDeviceId.text = "设备 ID: ${session.deviceId}"

        b.btnSave.setOnClickListener {
            val server = b.etServer.text?.toString()?.trim().orEmpty()
            val nick = b.etNick.text?.toString()?.trim().orEmpty()
            if (server.isBlank() || nick.isBlank()) {
                Toast.makeText(this, "服务器地址和昵称都需要填", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val normalized = if (server.startsWith("http")) server else "http://$server"
            session.serverUrl = normalized
            session.nickname = nick
            Toast.makeText(this, "已保存（重启悬浮窗服务生效）", Toast.LENGTH_LONG).show()
            finish()
        }

        b.btnTest.setOnClickListener {
            val server = b.etServer.text?.toString()?.trim().orEmpty()
            if (server.isBlank()) return@setOnClickListener
            val temp = Session.get(this).also {
                it.serverUrl = if (server.startsWith("http")) server else "http://$server"
            }
            lifecycleScope.launch {
                val ok = withContext(Dispatchers.IO) { runCatching { ApiClient(temp).health() }.getOrDefault(false) }
                Toast.makeText(this@SettingsActivity,
                    if (ok) "✅ 连接成功" else "❌ 无法连接",
                    Toast.LENGTH_LONG).show()
            }
        }
    }
}

package com.example.noticefloat.remote

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import com.example.noticefloat.BuildConfig
import java.util.UUID

/**
 * 全局配置：设备匿名 UUID + 服务器地址 + 用户昵称。
 * 首次读取 device_id 时自动生成。
 * 首次读取 serverUrl 时回退到 BuildConfig.DEFAULT_SERVER_URL（APK 内置的公网地址）。
 */
class Session private constructor(private val prefs: SharedPreferences) {

    var deviceId: String
        get() {
            var v = prefs.getString(KEY_DEVICE_ID, null)
            if (v.isNullOrBlank()) {
                v = "d-" + UUID.randomUUID().toString().replace("-", "").substring(0, 20)
                prefs.edit().putString(KEY_DEVICE_ID, v).apply()
            }
            return v
        }
        set(value) { prefs.edit().putString(KEY_DEVICE_ID, value).apply() }

    var serverUrl: String
        get() {
            val v = prefs.getString(KEY_SERVER, "").orEmpty()
            return v.ifBlank { BuildConfig.DEFAULT_SERVER_URL }
        }
        set(value) { prefs.edit().putString(KEY_SERVER, value.trimEnd('/')).apply() }

    var nickname: String
        get() {
            val v = prefs.getString(KEY_NICK, "").orEmpty()
            return v.ifBlank { defaultNickname() }
        }
        set(value) { prefs.edit().putString(KEY_NICK, value).apply() }

    /** v0.8.6 缓存的用户角色：super / admin / user；默认 user */
    var role: String
        get() = prefs.getString(KEY_ROLE, "user").orEmpty().ifBlank { "user" }
        set(value) { prefs.edit().putString(KEY_ROLE, value).apply() }

    private fun defaultNickname(): String {
        val m = Build.MODEL?.takeIf { it.isNotBlank() } ?: "Android"
        return "$m-" + deviceId.takeLast(4)
    }

    fun isConfigured(): Boolean = serverUrl.isNotBlank()

    val wsUrl: String
        get() = serverUrl.replaceFirst(Regex("^http"), "ws") + "/ws/$deviceId"

    /**
     * v0.8.15 从 GitHub raw 拉取最新的 cpolar URL，成功则覆盖 SharedPreferences。
     * cpolar 免费版每天变 URL 太麻烦，改用固定的 GitHub raw 存当前 URL，
     * cpolar 变了只需 push server_url.txt，客户端下次启动自动同步。
     * 先试 jsdelivr（国内 CDN 更快），失败再试 raw.githubusercontent.com。
     * v0.8.15.1 加域名白名单：只接受 *.cpolar.cn / *.cpolar.io / *.cpolar.top /
     *   localhost / 127.0.0.1，防止 server_url.txt 被篡改劫持客户端到恶意后端。
     * 阻塞 IO，请在协程/后台线程调用。
     */
    fun bootstrapServerUrl(): Boolean {
        val client = okhttp3.OkHttpClient.Builder()
            .connectTimeout(5, java.util.concurrent.TimeUnit.SECONDS)
            .readTimeout(5, java.util.concurrent.TimeUnit.SECONDS)
            .build()
        val urls = listOf(
            "https://cdn.jsdelivr.net/gh/DKaisa/noticefloat@main/server_url.txt",
            "https://raw.githubusercontent.com/DKaisa/noticefloat/main/server_url.txt",
        )
        val allowedSuffix = listOf(".cpolar.cn", ".cpolar.io", ".cpolar.top")
        val allowedHost = listOf("localhost", "127.0.0.1")
        for (u in urls) {
            try {
                val req = okhttp3.Request.Builder().url(u).get().build()
                client.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) return@use
                    val text = resp.body?.string().orEmpty()
                    val newUrl = text.lineSequence().map { it.trim() }.firstOrNull { it.startsWith("http") }
                    if (newUrl.isNullOrBlank()) return@use
                    // 域名白名单校验
                    val host = try {
                        java.net.URI(newUrl).host?.lowercase().orEmpty()
                    } catch (_: Exception) {
                        ""
                    }
                    val trusted = host in allowedHost || allowedSuffix.any { host.endsWith(it) }
                    if (!trusted) return@use
                    if (newUrl != serverUrl) {
                        serverUrl = newUrl
                        return true
                    }
                    return false
                }
            } catch (_: Exception) {
                // 试下一个 URL
            }
        }
        return false
    }

    companion object {
        private const val PREFS = "notice_session"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_SERVER = "server_url"
        private const val KEY_NICK = "nickname"
        private const val KEY_ROLE = "role"

        @Volatile private var INSTANCE: Session? = null
        fun get(context: Context): Session = INSTANCE ?: synchronized(this) {
            INSTANCE ?: Session(context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)).also { INSTANCE = it }
        }
    }
}


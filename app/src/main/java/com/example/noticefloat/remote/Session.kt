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

    private fun defaultNickname(): String {
        val m = Build.MODEL?.takeIf { it.isNotBlank() } ?: "Android"
        return "$m-" + deviceId.takeLast(4)
    }

    fun isConfigured(): Boolean = serverUrl.isNotBlank()

    val wsUrl: String
        get() = serverUrl.replaceFirst(Regex("^http"), "ws") + "/ws/$deviceId"

    companion object {
        private const val PREFS = "notice_session"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_SERVER = "server_url"
        private const val KEY_NICK = "nickname"

        @Volatile private var INSTANCE: Session? = null
        fun get(context: Context): Session = INSTANCE ?: synchronized(this) {
            INSTANCE ?: Session(context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)).also { INSTANCE = it }
        }
    }
}


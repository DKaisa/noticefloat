package com.example.noticefloat.remote

import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * WebSocket 长连：断线自动重连。收到消息回调 onEvent(type, payload)。
 */
class WsClient(
    private val session: Session,
    private val onEvent: (type: String, payload: JSONObject) -> Unit,
    private val onStatus: (connected: Boolean) -> Unit = {}
) {
    private val TAG = "NoticeWs"
    private val client = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    @Volatile private var ws: WebSocket? = null
    @Volatile private var closedByUser = false
    private var retryDelay = 2_000L

    fun start() {
        closedByUser = false
        connect()
    }

    fun stop() {
        closedByUser = true
        ws?.close(1000, "bye")
        ws = null
    }

    private fun connect() {
        if (closedByUser) return
        val url = session.wsUrl
        if (url.isBlank() || !url.startsWith("ws")) {
            Log.w(TAG, "无有效 WS 地址: $url")
            return
        }
        val req = Request.Builder().url(url).build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "WS connected")
                retryDelay = 2_000L
                onStatus(true)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val obj = JSONObject(text)
                    val type = obj.optString("type")
                    onEvent(type, obj)
                } catch (e: Exception) {
                    Log.w(TAG, "非 JSON 消息: $text")
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "WS failure: ${t.message}")
                onStatus(false)
                scheduleReconnect()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "WS closed: $code $reason")
                onStatus(false)
                if (!closedByUser) scheduleReconnect()
            }
        })
    }

    private fun scheduleReconnect() {
        if (closedByUser) return
        val delay = retryDelay
        retryDelay = (retryDelay * 2).coerceAtMost(30_000L)
        Thread {
            try { Thread.sleep(delay) } catch (_: InterruptedException) {}
            if (!closedByUser) connect()
        }.start()
    }
}

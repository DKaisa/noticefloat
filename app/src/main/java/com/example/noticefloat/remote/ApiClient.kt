package com.example.noticefloat.remote

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** 极简 REST 客户端：手写 JSON，不引入 Moshi/Retrofit。 */
class ApiClient(private val session: Session) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    private val json = "application/json; charset=utf-8".toMediaType()

    private fun url(path: String) = session.serverUrl + path

    @Throws(IOException::class)
    private fun post(path: String, body: JSONObject): JSONObject {
        val req = Request.Builder()
            .url(url(path))
            .post(body.toString().toRequestBody(json))
            .build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) throw IOException("HTTP ${resp.code}: $text")
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        }
    }

    @Throws(IOException::class)
    private fun get(path: String): JSONObject {
        val req = Request.Builder().url(url(path)).get().build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) throw IOException("HTTP ${resp.code}: $text")
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        }
    }

    fun health(): Boolean {
        return try { get("/api/health").optBoolean("ok", false) } catch (_: Exception) { false }
    }

    /** 建群，返回 (code, token, name) */
    fun createGroup(name: String): Triple<String, String, String> {
        val body = JSONObject()
            .put("device_id", session.deviceId)
            .put("nickname", session.nickname)
            .put("group_name", name)
        val r = post("/api/groups", body)
        return Triple(r.getString("code"), r.getString("token"), r.getString("name"))
    }

    fun joinGroup(code: String): String {
        val body = JSONObject()
            .put("device_id", session.deviceId)
            .put("nickname", session.nickname)
        val r = post("/api/groups/$code/join", body)
        return r.getString("name")
    }

    fun myGroups(): List<Triple<String, String, Int>> {
        val r = get("/api/devices/${session.deviceId}/groups")
        val arr = r.optJSONArray("groups") ?: JSONArray()
        return (0 until arr.length()).map { i ->
            val g = arr.getJSONObject(i)
            Triple(g.getString("code"), g.getString("name"), g.optInt("member_count", 1))
        }
    }

    fun leaveGroup(code: String) {
        post("/api/groups/$code/leave", JSONObject().put("device_id", session.deviceId))
    }

    /** 发任务，返回服务端 task id */
    fun publishTask(code: String, title: String, content: String, remindAt: Long, urgent: Boolean = false): Long {
        val body = JSONObject()
            .put("device_id", session.deviceId)
            .put("title", title)
            .put("content", content)
            .put("remind_at", remindAt)
            .put("urgent", urgent)
        val r = post("/api/groups/$code/tasks", body)
        return r.getLong("id")
    }

    fun markDone(serverTaskId: Long) {
        post("/api/tasks/$serverTaskId/done", JSONObject().put("device_id", session.deviceId))
    }
}

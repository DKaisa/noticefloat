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

    fun joinGroup(code: String): JSONObject {
        val body = JSONObject()
            .put("device_id", session.deviceId)
            .put("nickname", session.nickname)
        return post("/api/groups/$code/join", body)
    }

    /** v0.8.5 查角色：super/admin/user */
    fun getRole(): JSONObject {
        return get("/api/role/${session.deviceId}")
    }

    fun myGroups(): List<Triple<String, String, Int>> {
        val r = get("/api/devices/${session.deviceId}/groups")
        val arr = r.optJSONArray("groups") ?: JSONArray()
        return (0 until arr.length()).map { i ->
            val g = arr.getJSONObject(i)
            Triple(g.getString("code"), g.getString("name"), g.optInt("member_count", 1))
        }
    }

    /** v0.8.15 发现群列表：code, name, memberCount, joined, creatorName */
    data class DiscoverGroup(
        val code: String,
        val name: String,
        val memberCount: Int,
        val joined: Boolean,
        val creatorName: String,
    )

    fun discoverGroups(): Pair<List<DiscoverGroup>, Pair<Int, Int>> {
        val r = get("/api/groups/discover?device_id=${session.deviceId}")
        val arr = r.optJSONArray("groups") ?: JSONArray()
        val list = (0 until arr.length()).map { i ->
            val g = arr.getJSONObject(i)
            DiscoverGroup(
                code = g.getString("code"),
                name = g.getString("name"),
                memberCount = g.optInt("member_count", 1),
                joined = g.optInt("joined", 0) == 1,
                creatorName = g.optString("creator_name", ""),
            )
        }
        return list to (r.optInt("total", list.size) to r.optInt("max", 20))
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

    /** v0.8.3 广播任务（发给所有 device，无需加群），返回服务端 task id */
    fun publishBroadcast(title: String, content: String, remindAt: Long, urgent: Boolean = false): Long {
        val body = JSONObject()
            .put("device_id", session.deviceId)
            .put("nickname", session.nickname)
            .put("title", title)
            .put("content", content)
            .put("remind_at", remindAt)
            .put("urgent", urgent)
        val r = post("/api/broadcast", body)
        return r.getLong("id")
    }

    fun markDone(serverTaskId: Long) {
        post("/api/tasks/$serverTaskId/done", JSONObject().put("device_id", session.deviceId))
    }

    /** 亮屏/重连时拉取未读任务（since 为毫秒时间戳，0 表示全量） */
    fun pullUnread(sinceMs: Long): JSONArray {
        val r = get("/api/devices/${session.deviceId}/unread?since=$sinceMs")
        return r.optJSONArray("tasks") ?: JSONArray()
    }

    /** v0.8.6 查询后端最新 APK 版本 */
    fun latestApk(): JSONObject = get("/api/latest_apk")

    /** v0.8.6 下载 APK 到指定 File；返回是否成功 */
    fun downloadApk(relativeUrl: String, dest: java.io.File, onProgress: ((Long, Long) -> Unit)? = null): Boolean {
        val fullUrl = if (relativeUrl.startsWith("http")) relativeUrl else session.serverUrl + relativeUrl
        val req = Request.Builder().url(fullUrl).get().build()
        client.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) return false
            val body = resp.body ?: return false
            val total = body.contentLength()
            dest.outputStream().use { out ->
                body.byteStream().use { input ->
                    val buf = ByteArray(64 * 1024)
                    var read: Int
                    var written = 0L
                    while (input.read(buf).also { read = it } > 0) {
                        out.write(buf, 0, read)
                        written += read
                        onProgress?.invoke(written, total)
                    }
                }
            }
            return true
        }
    }
}

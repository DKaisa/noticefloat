package com.example.noticefloat.data

class TaskRepository(private val dao: TaskDao) {
    fun observePending() = dao.observePending()
    fun observeAll() = dao.observeAll()
    fun observePendingCount() = dao.observePendingCount()

    suspend fun add(task: Task): Long = dao.insert(task)
    suspend fun markDone(id: Long) = dao.setStatus(id, 1)
    suspend fun markExpired(id: Long) = dao.setStatus(id, 2)
    suspend fun getById(id: Long) = dao.getById(id)
    suspend fun getByServerId(serverId: Long) = dao.getByServerId(serverId)
    suspend fun delete(id: Long) = dao.delete(id)
    suspend fun update(task: Task) = dao.update(task)

    /** 服务端推来的任务：如已存在则跳过，避免重复。返回本地 id 或 -1 */
    suspend fun upsertFromServer(task: Task): Long {
        val serverId = task.serverId ?: return -1L
        val existing = dao.getByServerId(serverId)
        return if (existing != null) existing.id else dao.insert(task)
    }
}

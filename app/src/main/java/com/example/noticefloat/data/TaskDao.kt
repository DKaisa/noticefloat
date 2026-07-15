package com.example.noticefloat.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface TaskDao {
    @Query("SELECT * FROM tasks WHERE status = 0 ORDER BY remindAt ASC, createdAt DESC")
    fun observePending(): Flow<List<Task>>

    @Query("SELECT * FROM tasks ORDER BY createdAt DESC")
    fun observeAll(): Flow<List<Task>>

    @Query("SELECT COUNT(*) FROM tasks WHERE status = 0")
    fun observePendingCount(): Flow<Int>

    @Insert
    suspend fun insert(task: Task): Long

    @Update
    suspend fun update(task: Task)

    @Query("UPDATE tasks SET status = :status WHERE id = :id")
    suspend fun setStatus(id: Long, status: Int)

    @Query("SELECT * FROM tasks WHERE id = :id LIMIT 1")
    suspend fun getById(id: Long): Task?

    /** 通过服务端 id 查询本地对应记录（用于去重） */
    @Query("SELECT * FROM tasks WHERE serverId = :serverId LIMIT 1")
    suspend fun getByServerId(serverId: Long): Task?

    @Query("DELETE FROM tasks WHERE id = :id")
    suspend fun delete(id: Long)

    /** v0.8.6：按发布者删除（用于升级 task 去重） */
    @Query("DELETE FROM tasks WHERE publisher = :publisher")
    suspend fun deleteByPublisher(publisher: String)

    /** v0.8.6：按发布者查询首条（用于判断升级 task 是否已存在） */
    @Query("SELECT * FROM tasks WHERE publisher = :publisher LIMIT 1")
    suspend fun getByPublisher(publisher: String): Task?
}

package com.example.noticefloat.data

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 单条待办任务。
 * status: 0=待处理, 1=已完成, 2=已过期
 * source: local / group
 * serverId: 服务端主键（仅当 source=group）
 */
@Entity(tableName = "tasks")
data class Task(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val title: String,
    val content: String = "",
    /** 触发时间戳 (毫秒)。0 表示立即 / 无预约。 */
    val remindAt: Long = 0,
    val createdAt: Long = System.currentTimeMillis(),
    val status: Int = 0,
    /** 发布者名字（后续可扩展账号系统） */
    val publisher: String = "我",
    /** 来源：local / group */
    val source: String = "local",
    /** 所属群号（source=group 时非空） */
    val groupCode: String? = null,
    /** 服务端任务 id（source=group 时非空） */
    val serverId: Long? = null,
    /** 强弹窗：true = 收到后立即在最上层弹一个需手动关闭的卡片 */
    val urgent: Boolean = false
)

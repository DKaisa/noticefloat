package com.example.noticefloat

import android.app.Application
import com.example.noticefloat.data.AppDatabase
import com.example.noticefloat.data.TaskRepository

class NoticeApp : Application() {
    val database: AppDatabase by lazy { AppDatabase.getInstance(this) }
    val repository: TaskRepository by lazy { TaskRepository(database.taskDao()) }
}

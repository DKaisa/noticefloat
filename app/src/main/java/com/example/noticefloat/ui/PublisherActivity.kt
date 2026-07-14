package com.example.noticefloat.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.example.noticefloat.NoticeApp
import com.example.noticefloat.data.Task
import com.example.noticefloat.databinding.ActivityPublisherBinding
import com.example.noticefloat.reminder.ReminderScheduler
import com.example.noticefloat.remote.ApiClient
import com.example.noticefloat.remote.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

class PublisherActivity : AppCompatActivity() {
    private lateinit var b: ActivityPublisherBinding
    private var remindAtMillis: Long = 0L
    private val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA)
    private var groups: List<Triple<String, String, Int>> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityPublisherBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.btnClose.setOnClickListener { finish() }
        b.btnPickTime.setOnClickListener { pickDateTime() }
        b.btnClearTime.setOnClickListener {
            remindAtMillis = 0
            b.tvTime.text = "未设置（立即入待办）"
        }
        b.chipIn30m.setOnClickListener { quick(30) }
        b.chipIn1h.setOnClickListener { quick(60) }
        b.chipIn3h.setOnClickListener { quick(180) }
        b.chipTomorrow9.setOnClickListener { tomorrow9() }
        b.btnPublish.setOnClickListener { submit() }

        loadGroups()
    }

    private fun loadGroups() {
        val session = Session.get(this)
        if (!session.isConfigured()) {
            b.spTarget.adapter = ArrayAdapter(this,
                android.R.layout.simple_spinner_dropdown_item,
                listOf("本地私有（未配置服务器）"))
            return
        }
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) {
                runCatching { ApiClient(session).myGroups() }.getOrDefault(emptyList())
            }
            groups = r
            val labels = mutableListOf("本地私有（仅自己看到）")
            labels.addAll(r.map { "群「${it.second}」 #${it.first} · ${it.third}人" })
            b.spTarget.adapter = ArrayAdapter(this@PublisherActivity,
                android.R.layout.simple_spinner_dropdown_item, labels)
        }
    }

    private fun quick(minutes: Int) {
        remindAtMillis = System.currentTimeMillis() + minutes * 60_000L
        b.tvTime.text = fmt.format(Date(remindAtMillis))
    }

    private fun tomorrow9() {
        val c = Calendar.getInstance().apply {
            add(Calendar.DAY_OF_MONTH, 1)
            set(Calendar.HOUR_OF_DAY, 9); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0)
        }
        remindAtMillis = c.timeInMillis
        b.tvTime.text = fmt.format(Date(remindAtMillis))
    }

    private fun pickDateTime() {
        val now = Calendar.getInstance()
        val dlgTheme = android.R.style.Theme_DeviceDefault_Light_Dialog
        DatePickerDialog(this, dlgTheme, { _, y, m, d ->
            TimePickerDialog(this, dlgTheme, { _, hh, mm ->
                val c = Calendar.getInstance()
                c.set(y, m, d, hh, mm, 0)
                remindAtMillis = c.timeInMillis
                b.tvTime.text = fmt.format(Date(remindAtMillis))
            }, now.get(Calendar.HOUR_OF_DAY), now.get(Calendar.MINUTE), true).show()
        }, now.get(Calendar.YEAR), now.get(Calendar.MONTH), now.get(Calendar.DAY_OF_MONTH)).show()
    }

    private fun submit() {
        val title = b.etTitle.text?.toString()?.trim().orEmpty()
        val content = b.etContent.text?.toString()?.trim().orEmpty()
        if (title.isEmpty()) {
            Toast.makeText(this, "请填写标题", Toast.LENGTH_SHORT).show()
            return
        }
        val idx = b.spTarget.selectedItemPosition
        val target = if (idx <= 0 || idx - 1 !in groups.indices) null else groups[idx - 1]
        val urgent = b.cbUrgent.isChecked

        lifecycleScope.launch {
            if (target == null) {
                val id = withContext(Dispatchers.IO) {
                    (application as NoticeApp).repository.add(
                        Task(title = title, content = content, remindAt = remindAtMillis, urgent = urgent)
                    )
                }
                if (remindAtMillis > 0) ReminderScheduler.schedule(this@PublisherActivity, id, remindAtMillis)
                done()
            } else {
                val session = Session.get(this@PublisherActivity)
                val result = withContext(Dispatchers.IO) {
                    runCatching {
                        val serverId = ApiClient(session)
                            .publishTask(target.first, title, content, remindAtMillis, urgent)
                        val local = Task(
                            title = title, content = content, remindAt = remindAtMillis,
                            publisher = session.nickname, source = "group",
                            groupCode = target.first, serverId = serverId, urgent = urgent
                        )
                        (application as NoticeApp).repository.upsertFromServer(local)
                    }
                }
                result.onSuccess { localId ->
                    if (remindAtMillis > 0 && localId > 0)
                        ReminderScheduler.schedule(this@PublisherActivity, localId, remindAtMillis)
                    done()
                }.onFailure {
                    Toast.makeText(this@PublisherActivity, "发送失败：${it.message}", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun done() {
        Toast.makeText(this, "已发布", Toast.LENGTH_SHORT).show()
        finish()
    }
}

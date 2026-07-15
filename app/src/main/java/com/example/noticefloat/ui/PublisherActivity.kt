package com.example.noticefloat.ui

import android.Manifest
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
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
    private var role: String = "user"       // v0.8.5: super / admin / user

    companion object {
        const val EXTRA_PREFILL_CONTENT = "prefill_content"
        const val EXTRA_START_VOICE = "start_voice"
        private const val REQ_MIC = 2001
    }

    private var recognizer: SpeechRecognizer? = null
    private var listening = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityPublisherBinding.inflate(layoutInflater)
        setContentView(b.root)

        // v0.8.10：进入发布界面时，收起悬浮的"快捷发布"面板，避免它盖在编辑框上
        com.example.noticefloat.service.FloatingService.hideQuickPanel(this)

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

        // v0.8: 语音输入预填
        intent.getStringExtra(EXTRA_PREFILL_CONTENT)?.takeIf { it.isNotBlank() }?.let { pre ->
            b.etContent.setText(pre)
            b.etContent.setSelection(pre.length)
        }

        // v0.8.3: 语音停止/重录按钮
        b.btnVoiceStop.setOnClickListener {
            if (listening) {
                try { recognizer?.stopListening() } catch (_: Exception) {}
            } else {
                startVoice()
            }
        }

        loadGroups()

        // v0.8.3: 语音自动启动
        if (intent.getBooleanExtra(EXTRA_START_VOICE, false)) {
            ensureMicPermissionThenStartVoice()
        }
    }

    private fun ensureMicPermissionThenStartVoice() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
        } else {
            startVoice()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                startVoice()
            } else {
                Toast.makeText(this, "未授予录音权限", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun startVoice() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            setVoiceStatus("系统无可用语音识别引擎，可用键盘话筒代替")
            return
        }
        setVoiceStatus("🎙 聆听中…说完后点【停止/重录】")
        try {
            recognizer?.destroy()
            recognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
                setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) {}
                    override fun onBeginningOfSpeech() {}
                    override fun onRmsChanged(rmsdB: Float) {}
                    override fun onBufferReceived(buffer: ByteArray?) {}
                    override fun onEndOfSpeech() { setVoiceStatus("识别中…") }
                    override fun onError(error: Int) {
                        listening = false
                        val msg = when (error) {
                            SpeechRecognizer.ERROR_NO_MATCH -> "没听清，请重试"
                            SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "没检测到语音"
                            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "缺少录音权限"
                            SpeechRecognizer.ERROR_NETWORK,
                            SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
                                "网络异常（可用键盘话筒代替）"
                            SpeechRecognizer.ERROR_CLIENT -> "识别服务出错"
                            SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "识别引擎繁忙"
                            SpeechRecognizer.ERROR_SERVER -> "识别服务不可用"
                            else -> "识别失败 (code=$error)"
                        }
                        setVoiceStatus("❌ $msg  点【停止/重录】重试")
                    }
                    override fun onResults(results: Bundle?) {
                        listening = false
                        val list = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        val text = list?.firstOrNull().orEmpty().trim()
                        if (text.isBlank()) {
                            setVoiceStatus("❌ 未识别到内容，点【停止/重录】重试")
                        } else {
                            val cur = b.etContent.text?.toString().orEmpty()
                            val merged = if (cur.isBlank()) text else "$cur $text"
                            b.etContent.setText(merged)
                            b.etContent.setSelection(merged.length)
                            setVoiceStatus("✔ 已识别，可继续【停止/重录】或修改后发布")
                        }
                    }
                    override fun onPartialResults(partial: Bundle?) {
                        val list = partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        val text = list?.firstOrNull().orEmpty()
                        if (text.isNotEmpty()) setVoiceStatus("🎙 $text")
                    }
                    override fun onEvent(eventType: Int, params: Bundle?) {}
                })
            }
            val recIntent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "zh-CN")
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, packageName)
                putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            }
            recognizer?.startListening(recIntent)
            listening = true
        } catch (e: Exception) {
            e.printStackTrace()
            listening = false
            setVoiceStatus("❌ 启动语音识别失败: ${e.message}")
        }
    }

    private fun setVoiceStatus(text: String) {
        b.voiceBar.visibility = View.VISIBLE
        b.tvVoiceStatus.text = text
    }

    override fun onDestroy() {
        super.onDestroy()
        try { recognizer?.destroy() } catch (_: Exception) {}
        recognizer = null
    }

    private fun loadGroups() {
        val session = Session.get(this)
        if (!session.isConfigured()) {
            b.spTarget.adapter = ArrayAdapter(this,
                android.R.layout.simple_spinner_dropdown_item,
                listOf("仅自己（未配置服务器）"))
            return
        }
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) {
                runCatching { ApiClient(session).myGroups() }.getOrDefault(emptyList())
            }
            val roleJson = withContext(Dispatchers.IO) {
                runCatching { ApiClient(session).getRole() }.getOrNull()
            }
            role = roleJson?.optString("role") ?: "user"
            groups = r
            val labels = mutableListOf<String>()
            if (role == "user") {
                // 普通用户：只能"仅自己"
                labels.add("仅自己（普通用户仅本机提醒）")
            } else {
                labels.add("📢 所有人（广播）")
                labels.addAll(r.map { "群「${it.second}」 #${it.first} · ${it.third}人" })
                labels.add("仅自己（不推送给他人）")
            }
            b.spTarget.adapter = ArrayAdapter(this@PublisherActivity,
                android.R.layout.simple_spinner_dropdown_item, labels)
            b.spTarget.setSelection(0)
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
        DatePickerDialog(this, { _, y, m, d ->
            // 使用 spinner 主题让年/月/日、时/分都能明确输入
            val tp = TimePickerDialog(this,
                android.R.style.Theme_Holo_Light_Dialog,
                { _, hh, mm ->
                    val c = Calendar.getInstance()
                    c.set(y, m, d, hh, mm, 0)
                    remindAtMillis = c.timeInMillis
                    b.tvTime.text = fmt.format(Date(remindAtMillis))
                }, now.get(Calendar.HOUR_OF_DAY), now.get(Calendar.MINUTE), true)
            tp.show()
        }, now.get(Calendar.YEAR), now.get(Calendar.MONTH), now.get(Calendar.DAY_OF_MONTH)).show()
    }

    private fun submit() {
        val content = b.etContent.text?.toString()?.trim().orEmpty()
        if (content.isEmpty()) {
            Toast.makeText(this, "请填写消息内容", Toast.LENGTH_SHORT).show()
            return
        }
        // 后端 API 仍要 title，取首行/前 30 字作为 title
        val firstLine = content.lineSequence().firstOrNull()?.trim().orEmpty()
        val title = if (firstLine.length <= 30) firstLine else firstLine.substring(0, 30) + "…"
        val bodyRest = if (content.length > title.length) content else ""
        val idx = b.spTarget.selectedItemPosition
        // v0.8.5: 普通用户 spinner 只有 "仅自己"，一律走本地
        val isUserRole = (role == "user")
        // spinner 布局（admin/super）：[0]=📢 广播，[1..groups.size]=群，最后一项=仅自己
        val broadcast = !isUserRole && (idx == 0)
        val target: Triple<String, String, Int>? = when {
            isUserRole -> null
            broadcast -> null
            groups.isNotEmpty() && idx in 1..groups.size -> groups[idx - 1]
            else -> null
        }
        val isLocalOnly = isUserRole || (!broadcast && target == null)
        val urgent = b.cbUrgent.isChecked

        lifecycleScope.launch {
            if (broadcast) {
                val session = Session.get(this@PublisherActivity)
                if (!session.isConfigured()) {
                    Toast.makeText(this@PublisherActivity, "未配置服务器，无法广播", Toast.LENGTH_LONG).show()
                    return@launch
                }
                val result = withContext(Dispatchers.IO) {
                    runCatching {
                        val serverId = ApiClient(session)
                            .publishBroadcast(title, bodyRest, remindAtMillis, urgent)
                        val local = Task(
                            title = title, content = bodyRest, remindAt = remindAtMillis,
                            publisher = session.nickname, source = "broadcast",
                            groupCode = "*", serverId = serverId, urgent = urgent
                        )
                        (application as NoticeApp).repository.upsertFromServer(local)
                    }
                }
                result.onSuccess { localId ->
                    if (remindAtMillis > 0 && localId > 0)
                        ReminderScheduler.schedule(this@PublisherActivity, localId, remindAtMillis)
                    Toast.makeText(this@PublisherActivity, "📢 已广播给所有人", Toast.LENGTH_SHORT).show()
                    finish()
                }.onFailure {
                    Toast.makeText(this@PublisherActivity, "广播失败：${it.message}", Toast.LENGTH_LONG).show()
                }
            } else if (isLocalOnly) {
                val id = withContext(Dispatchers.IO) {
                    (application as NoticeApp).repository.add(
                        Task(title = title, content = bodyRest, remindAt = remindAtMillis, urgent = urgent)
                    )
                }
                if (remindAtMillis > 0) ReminderScheduler.schedule(this@PublisherActivity, id, remindAtMillis)
                done()
            } else {
                val session = Session.get(this@PublisherActivity)
                val grp = target!!
                val result = withContext(Dispatchers.IO) {
                    runCatching {
                        val serverId = ApiClient(session)
                            .publishTask(grp.first, title, bodyRest, remindAtMillis, urgent)
                        val local = Task(
                            title = title, content = bodyRest, remindAt = remindAtMillis,
                            publisher = session.nickname, source = "group",
                            groupCode = grp.first, serverId = serverId, urgent = urgent
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

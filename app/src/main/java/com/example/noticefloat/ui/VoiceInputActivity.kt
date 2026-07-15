package com.example.noticefloat.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/**
 * 语音输入 Activity（v0.8）：
 * - 长按悬浮球触发
 * - 请求 RECORD_AUDIO 权限
 * - 使用系统 SpeechRecognizer 识别
 * - 识别成功后展示可编辑文本 + [发送] 按钮
 * - [发送] = 跳 PublisherActivity 预填 content
 */
class VoiceInputActivity : AppCompatActivity() {

    private lateinit var tvStatus: TextView
    private lateinit var tvHint: TextView
    private lateinit var etResult: EditText
    private lateinit var btnCancel: Button
    private lateinit var btnStop: Button
    private lateinit var btnSend: Button

    private var recognizer: SpeechRecognizer? = null
    private var listening = false
    private var fallbackTried = false

    companion object {
        private const val REQ_MIC = 1001
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(com.example.noticefloat.R.layout.activity_voice_input)
        tvStatus = findViewById(com.example.noticefloat.R.id.tvStatus)
        tvHint = findViewById(com.example.noticefloat.R.id.tvHint)
        etResult = findViewById(com.example.noticefloat.R.id.etResult)
        btnCancel = findViewById(com.example.noticefloat.R.id.btnCancel)
        btnStop = findViewById(com.example.noticefloat.R.id.btnStop)
        btnSend = findViewById(com.example.noticefloat.R.id.btnSend)

        btnCancel.setOnClickListener { finish() }
        btnStop.setOnClickListener { stopListening() }
        btnSend.setOnClickListener { sendToPublisher() }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this, arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC
            )
        } else {
            startListening()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                startListening()
            } else {
                Toast.makeText(this, "未授予录音权限", Toast.LENGTH_SHORT).show()
                finish()
            }
        }
    }

    private fun startListening() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            Toast.makeText(this, "系统无可用语音识别引擎", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        recognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
            setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) {
                    tvStatus.text = "🎙 聆听中…请说话"
                }
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() {
                    tvStatus.text = "识别中…"
                }
                override fun onError(error: Int) {
                    listening = false
                    // v0.8.6：华为/EMUI 常见 code=12 ERROR_LANGUAGE_UNAVAILABLE，自动兜底重试
                    if (error == 12 && !fallbackTried) {
                        fallbackTried = true
                        tvStatus.text = "🎙 已切换到系统默认语言，重试中…"
                        startListening()
                        return
                    }
                    val msg = when (error) {
                        SpeechRecognizer.ERROR_NO_MATCH -> "没听清，请重试"
                        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "没检测到语音"
                        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "缺少录音权限"
                        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "网络异常"
                        SpeechRecognizer.ERROR_CLIENT -> "识别服务出错"
                        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "识别引擎繁忙"
                        11 -> "手机语音引擎太老（server 端不支持），请在系统设置里更新语音引擎"
                        12 -> "手机不支持中文识别语言包，请到系统设置→通用→语言/语音下载"
                        else -> "识别失败 (code=$error)"
                    }
                    tvStatus.text = "❌ $msg"
                    tvHint.text = "可点【停止】重试或【取消】关闭"
                }
                override fun onResults(results: Bundle?) {
                    listening = false
                    val list = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    val text = list?.firstOrNull().orEmpty()
                    showResult(text)
                }
                override fun onPartialResults(partial: Bundle?) {
                    val list = partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    val text = list?.firstOrNull().orEmpty()
                    if (text.isNotEmpty()) tvHint.text = text
                }
                override fun onEvent(eventType: Int, params: Bundle?) {}
            })
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            if (!fallbackTried) {
                // 首次尝试：指定 zh-CN
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "zh-CN")
            } // 兜底时不指定 language，交给系统默认
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, packageName)
        }
        recognizer?.startListening(intent)
        listening = true
    }

    private fun stopListening() {
        if (listening) {
            recognizer?.stopListening()
        } else {
            // 已经在结果状态，重新开始
            etResult.visibility = android.view.View.GONE
            btnSend.visibility = android.view.View.GONE
            btnStop.text = "停止"
            tvStatus.text = "🎙 聆听中…请说话"
            tvHint.text = "说完后点【停止】识别"
            startListening()
        }
    }

    private fun showResult(text: String) {
        tvStatus.text = if (text.isBlank()) "❌ 没识别到内容" else "✔ 识别完成"
        tvHint.text = "可编辑后点【发送】"
        etResult.setText(text)
        etResult.setSelection(text.length)
        etResult.visibility = android.view.View.VISIBLE
        btnStop.text = "重录"
        btnSend.visibility = android.view.View.VISIBLE
    }

    private fun sendToPublisher() {
        val text = etResult.text?.toString()?.trim().orEmpty()
        if (text.isBlank()) {
            Toast.makeText(this, "内容为空", Toast.LENGTH_SHORT).show()
            return
        }
        val intent = Intent(this, PublisherActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            putExtra(PublisherActivity.EXTRA_PREFILL_CONTENT, text)
        }
        startActivity(intent)
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        recognizer?.destroy()
        recognizer = null
    }
}

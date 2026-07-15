package com.example.noticefloat.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/**
 * v0.8.2: 透明 Activity, 仅用于请求录音权限。
 * 授权后 Toast 提示"已授权，请再次长按录音"并 finish。
 */
class VoicePermissionActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) {
            finish()
            return
        }
        ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), 1)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "已授权，请再次长按悬浮球录音", Toast.LENGTH_SHORT).show()
        } else {
            Toast.makeText(this, "未授权录音权限，语音输入不可用", Toast.LENGTH_LONG).show()
        }
        finish()
    }
}

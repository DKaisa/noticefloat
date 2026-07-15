package com.example.noticefloat.ui

import android.app.Activity
import android.app.AlertDialog
import android.app.ProgressDialog
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.util.Log
import androidx.core.content.FileProvider
import com.example.noticefloat.BuildConfig
import com.example.noticefloat.remote.ApiClient
import com.example.noticefloat.remote.Session
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * v0.8.6：APK 自动升级检查
 * - MainActivity onResume 触发 checkAndPrompt()
 * - 后端 /api/latest_apk 返回 versionCode/url，若 > 当前则弹升级对话框
 */
object UpdateChecker {
    private const val TAG = "UpdateChecker"
    const val UPDATE_PUBLISHER = "__update__"

    fun checkAndPrompt(activity: Activity, silentIfNoUpdate: Boolean = true) {
        val api = ApiClient(Session.get(activity))
        val appCtx = activity.applicationContext
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val info = api.latestApk()
                val latestCode = info.optInt("versionCode", 0)
                val latestName = info.optString("versionName", "")
                val relUrl = info.optString("url", "")
                val available = info.optBoolean("available", false)
                val changelog = info.optString("changelog", "")
                val curCode = BuildConfig.VERSION_CODE
                Log.i(TAG, "latest=$latestCode cur=$curCode available=$available silent=$silentIfNoUpdate")
                if (latestCode > curCode && available && relUrl.isNotBlank()) {
                    if (silentIfNoUpdate) {
                        // 静默模式：往待办列表插一条"升级"消息，供用户点击触发
                        upsertUpdateTask(appCtx, latestName, relUrl, changelog)
                    } else {
                        withContext(Dispatchers.Main) {
                            promptDownload(activity, api, latestName, relUrl, changelog)
                        }
                    }
                } else {
                    // 无更新：清理旧的升级 task（防止老版本插入的一直挂着）
                    if (silentIfNoUpdate) {
                        (activity.application as? com.example.noticefloat.NoticeApp)
                            ?.repository?.deleteByPublisher(UPDATE_PUBLISHER)
                    } else {
                        withContext(Dispatchers.Main) {
                            AlertDialog.Builder(activity)
                                .setTitle("已是最新版本")
                                .setMessage("当前 v${BuildConfig.VERSION_NAME}，无需更新。")
                                .setPositiveButton("确定", null)
                                .show()
                        }
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "check failed: ${e.message}")
                if (!silentIfNoUpdate) {
                    withContext(Dispatchers.Main) {
                        android.widget.Toast.makeText(activity, "升级检查失败：${e.message}", android.widget.Toast.LENGTH_SHORT).show()
                    }
                }
            }
        }
    }

    /** 往本地待办插入一条"升级"消息（去重后重插一次以刷新版本号/日志） */
    private suspend fun upsertUpdateTask(
        appCtx: android.content.Context,
        latestName: String,
        relUrl: String,
        changelog: String
    ) {
        val app = appCtx as? com.example.noticefloat.NoticeApp ?: return
        val repo = app.repository
        repo.deleteByPublisher(UPDATE_PUBLISHER)
        val summary = if (changelog.isBlank()) "点击这条消息立即升级" else changelog
        repo.add(
            com.example.noticefloat.data.Task(
                title = "🚀 有新版本 v$latestName 可升级",
                content = summary,
                publisher = UPDATE_PUBLISHER,
                source = "local",
                // 复用 groupCode 字段透传 apk 相对 URL，点击时读取
                groupCode = relUrl
            )
        )
    }

    /** 点击"升级"待办时的入口：直接触发下载安装（需 Activity 上下文承载对话框/进度条） */
    fun downloadFromTask(activity: Activity, relUrl: String) {
        val api = ApiClient(Session.get(activity))
        download(activity, api, BuildConfig.VERSION_NAME, relUrl)
    }

    private fun promptDownload(activity: Activity, api: ApiClient, name: String, url: String, changelog: String) {
        AlertDialog.Builder(activity)
            .setTitle("发现新版本 v$name")
            .setMessage(if (changelog.isBlank()) "点击下载并升级" else changelog)
            .setPositiveButton("下载并安装") { _, _ -> download(activity, api, name, url) }
            .setNegativeButton("稍后", null)
            .setCancelable(false)
            .show()
    }

    private fun download(activity: Activity, api: ApiClient, name: String, url: String) {
        val fileName = "noticefloat-latest.apk"
        val outFile = File(activity.getExternalFilesDir(null), fileName)
        val progress = ProgressDialog(activity).apply {
            setTitle("正在下载 v$name")
            setMessage("请稍候…")
            setCancelable(false)
            setProgressStyle(ProgressDialog.STYLE_HORIZONTAL)
            max = 100
            show()
        }
        CoroutineScope(Dispatchers.IO).launch {
            val ok = try {
                api.downloadApk(url, outFile) { written, total ->
                    if (total > 0) {
                        val pct = (written * 100 / total).toInt()
                        activity.runOnUiThread { progress.progress = pct }
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "download failed", e); false
            }
            withContext(Dispatchers.Main) {
                progress.dismiss()
                if (ok && outFile.exists()) {
                    installApk(activity, outFile)
                } else {
                    android.widget.Toast.makeText(activity, "下载失败", android.widget.Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun installApk(activity: Activity, file: File) {
        try {
            val uri = FileProvider.getUriForFile(
                activity, activity.packageName + ".fileprovider", file
            )
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            // Android 8+ 需要"安装未知应用"权限
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                && !activity.packageManager.canRequestPackageInstalls()) {
                AlertDialog.Builder(activity)
                    .setTitle("需要授权")
                    .setMessage("请在下一步允许「安装未知来源应用」，然后返回重试")
                    .setPositiveButton("去授权") { _, _ ->
                        activity.startActivity(
                            Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                Uri.parse("package:${activity.packageName}"))
                        )
                    }
                    .setNegativeButton("取消", null)
                    .show()
                return
            }
            activity.startActivity(intent)
        } catch (e: Exception) {
            Log.e(TAG, "install failed", e)
            android.widget.Toast.makeText(activity, "拉起安装失败：${e.message}", android.widget.Toast.LENGTH_LONG).show()
        }
    }
}

package com.example.noticefloat.service

import android.app.usage.UsageStatsManager
import android.content.Context

/**
 * 通过系统 UsageStats 判断微信是否处于前台，不读取任何微信内部内容，合规。
 * 需要用户在系统设置里授予"有权查看使用情况"(PACKAGE_USAGE_STATS) 权限。
 */
object ForegroundAppDetector {
    const val WECHAT_PACKAGE = "com.tencent.mm"

    fun getForegroundPackage(context: Context): String? {
        val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as? UsageStatsManager
            ?: return null
        val end = System.currentTimeMillis()
        // 查询最近 10 秒的事件即可
        val begin = end - 10_000L
        val events = usm.queryEvents(begin, end)
        val event = android.app.usage.UsageEvents.Event()
        var lastPkg: String? = null
        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            if (event.eventType == android.app.usage.UsageEvents.Event.MOVE_TO_FOREGROUND ||
                event.eventType == android.app.usage.UsageEvents.Event.ACTIVITY_RESUMED
            ) {
                lastPkg = event.packageName
            }
        }
        return lastPkg
    }

    fun isWeChatForeground(context: Context): Boolean {
        return getForegroundPackage(context) == WECHAT_PACKAGE
    }
}

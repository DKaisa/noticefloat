package com.example.noticefloat.reminder

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.example.noticefloat.service.FloatingService

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            FloatingService.start(context.applicationContext)
        }
    }
}

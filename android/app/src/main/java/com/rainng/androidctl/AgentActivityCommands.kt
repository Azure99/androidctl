package com.rainng.androidctl

import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import com.rainng.androidctl.agent.service.AgentServerService

internal fun startAgentServer(context: Context) {
    ContextCompat.startForegroundService(context, startAgentServerIntent(context))
}

internal fun stopAgentServer(context: Context) {
    context.startService(stopAgentServerIntent(context))
}

internal fun openAgentAccessibilitySettings(activity: ComponentActivity) {
    activity.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
}

private fun startAgentServerIntent(context: Context): Intent =
    Intent(context, AgentServerService::class.java).apply {
        action = AgentServerService.ACTION_START
    }

private fun stopAgentServerIntent(context: Context): Intent =
    Intent(context, AgentServerService::class.java).apply {
        action = AgentServerService.ACTION_STOP
    }

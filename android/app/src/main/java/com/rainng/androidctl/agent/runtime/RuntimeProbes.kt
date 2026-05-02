package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityServiceInfo
import android.app.ActivityManager
import android.content.ComponentName
import android.content.Context
import android.view.accessibility.AccessibilityManager
import com.rainng.androidctl.agent.service.AgentServerService
import com.rainng.androidctl.agent.service.DeviceAccessibilityService

internal fun probeAccessibilityEnabled(
    context: Context,
    accessibilityServiceEnabledProbe: (Context) -> Boolean,
    warningLogger: (String) -> Unit,
): Boolean =
    try {
        accessibilityServiceEnabledProbe(context)
    } catch (error: SecurityException) {
        warningLogger("failed to probe accessibility state: ${error.message}")
        false
    }

internal fun probeServerRunning(
    context: Context,
    serverRunningProbe: (Context) -> Boolean,
    warningLogger: (String) -> Unit,
): Boolean =
    try {
        serverRunningProbe(context)
    } catch (error: SecurityException) {
        warningLogger("failed to probe server state: ${error.message}")
        false
    }

internal fun defaultIsAccessibilityServiceEnabled(context: Context): Boolean {
    val accessibilityManager =
        context.getSystemService(Context.ACCESSIBILITY_SERVICE) as? AccessibilityManager
            ?: return false
    val targetComponent = ComponentName(context, DeviceAccessibilityService::class.java)
    val enabledServices =
        accessibilityManager.getEnabledAccessibilityServiceList(
            AccessibilityServiceInfo.FEEDBACK_ALL_MASK,
        )
    return enabledServices.any { serviceInfo ->
        serviceInfo.resolveInfo?.serviceInfo?.let { service ->
            service.packageName == targetComponent.packageName && service.name == targetComponent.className
        } ?: false
    }
}

@Suppress("DEPRECATION")
internal fun defaultIsAgentServerRunning(context: Context): Boolean {
    val activityManager = context.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager ?: return false
    val targetComponent = ComponentName(context, AgentServerService::class.java)
    return activityManager.getRunningServices(Int.MAX_VALUE).any { serviceInfo ->
        serviceInfo.service == targetComponent
    }
}

package com.rainng.androidctl

import android.app.UiAutomation
import android.os.Build
import android.util.SparseArray
import android.view.accessibility.AccessibilityWindowInfo

internal class AccessibilityConnection(
    automation: UiAutomation,
) {
    private val connection =
        HiddenApi.clientConnection.invoke(null, HiddenApi.automationConnectionId.invoke(automation))
            ?: throw Rejected("backendConnection", "The UI service connection is unavailable")

    init {
        // App events and late prefetch callbacks must not supply a previous observation.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val cache =
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.VANILLA_ICE_CREAM) {
                    HiddenApi.automationCache.invoke(automation)
                } else {
                    HiddenApi.clientCache.invoke(null, HiddenApi.automationConnectionId.invoke(automation))
                } ?: throw Rejected("backendCache", "The UI node cache cannot be configured")
            HiddenApi.method(cache.javaClass, "setEnabled", Boolean::class.javaPrimitiveType!!).invoke(cache, false)
        }
    }

    fun windows(): List<AccessibilityWindowInfo> {
        val all =
            HiddenApi.windows.invoke(connection) as? SparseArray<*>
                ?: throw Rejected("windowsUnavailable", "Fresh window enumeration failed")
        val values = all[0] ?: return emptyList()
        return (values as List<*>).map { it as AccessibilityWindowInfo }
    }
}

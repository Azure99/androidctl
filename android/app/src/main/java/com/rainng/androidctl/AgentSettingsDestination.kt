package com.rainng.androidctl

import android.content.Intent
import android.provider.Settings
import androidx.core.net.toUri

internal fun appInfoSettingsDestination(packageName: String): AgentSettingsDestination {
    require(packageName.isNotBlank()) { "packageName is required" }
    return AgentSettingsDestination(
        action = Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
        dataString = "package:$packageName",
    )
}

internal fun batteryOptimizationSettingsDestination(): AgentSettingsDestination =
    AgentSettingsDestination(
        action = Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS,
    )

internal fun genericSystemSettingsDestination(): AgentSettingsDestination =
    AgentSettingsDestination(
        action = Settings.ACTION_SETTINGS,
    )

internal data class AgentSettingsDestination(
    val action: String,
    val dataString: String? = null,
)

internal fun AgentSettingsDestination.toIntent(): Intent =
    Intent(action).apply {
        dataString?.let { data = it.toUri() }
    }

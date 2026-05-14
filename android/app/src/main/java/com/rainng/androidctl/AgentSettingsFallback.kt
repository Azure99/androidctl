package com.rainng.androidctl

import android.content.ActivityNotFoundException
import androidx.activity.ComponentActivity

internal interface AgentSettingsEntryLauncher {
    fun canResolve(destination: AgentSettingsDestination): Boolean

    fun launch(destination: AgentSettingsDestination): Boolean
}

internal class AndroidAgentSettingsEntryLauncher(
    private val activity: ComponentActivity,
) : AgentSettingsEntryLauncher {
    override fun canResolve(destination: AgentSettingsDestination): Boolean =
        destination.toIntent().resolveActivity(activity.packageManager) != null

    override fun launch(destination: AgentSettingsDestination): Boolean {
        val intent = destination.toIntent()
        return try {
            activity.startActivity(intent)
            true
        } catch (_: ActivityNotFoundException) {
            false
        } catch (_: SecurityException) {
            false
        } catch (_: IllegalArgumentException) {
            false
        }
    }
}

internal fun openAgentAppInfo(activity: ComponentActivity) {
    openAgentSettingsWithFallbacks(
        launcher = AndroidAgentSettingsEntryLauncher(activity),
        destinations = appInfoSettingsFallbackChain(activity.packageName),
    )
}

internal fun openAgentBatteryOptimizationSettings(activity: ComponentActivity) {
    openAgentSettingsWithFallbacks(
        launcher = AndroidAgentSettingsEntryLauncher(activity),
        destinations = batteryOptimizationSettingsFallbackChain(activity.packageName),
    )
}

internal fun appInfoSettingsFallbackChain(packageName: String): List<AgentSettingsDestination> =
    listOf(
        appInfoSettingsDestination(packageName),
        genericSystemSettingsDestination(),
    )

internal fun batteryOptimizationSettingsFallbackChain(packageName: String): List<AgentSettingsDestination> =
    listOf(
        batteryOptimizationSettingsDestination(),
        appInfoSettingsDestination(packageName),
        genericSystemSettingsDestination(),
    )

internal fun openAgentSettingsWithFallbacks(
    launcher: AgentSettingsEntryLauncher,
    destinations: List<AgentSettingsDestination>,
) {
    for (destination in destinations) {
        if (!launcher.canResolve(destination)) {
            continue
        }
        if (launcher.launch(destination)) {
            return
        }
    }
}

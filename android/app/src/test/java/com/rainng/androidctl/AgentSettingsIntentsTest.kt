package com.rainng.androidctl

import android.provider.Settings
import org.junit.Assert.assertEquals
import org.junit.Test

class AgentSettingsIntentsTest {
    @Test
    fun appInfoDestinationTargetsCurrentPackageDetails() {
        val destination = appInfoSettingsDestination("com.rainng.androidctl")

        assertEquals(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, destination.action)
        assertEquals("package:com.rainng.androidctl", destination.dataString)
    }

    @Test
    fun batteryOptimizationFallbackChainTargetsBatteryThenAppInfoThenSystemSettings() {
        val actions = batteryOptimizationSettingsFallbackChain("com.rainng.androidctl").actions()

        assertEquals(
            listOf(
                Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS,
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Settings.ACTION_SETTINGS,
            ),
            actions,
        )
    }

    @Test
    fun appInfoFallbackChainTargetsAppInfoThenSystemSettings() {
        val actions = appInfoSettingsFallbackChain("com.rainng.androidctl").actions()

        assertEquals(
            listOf(
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Settings.ACTION_SETTINGS,
            ),
            actions,
        )
    }

    @Test
    fun batteryOptimizationFallbackMovesToAppInfoWhenPrimaryIsUnresolvable() {
        val launcher =
            RecordingAgentSettingsEntryLauncher(
                canResolveByAction =
                    mapOf(
                        Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS to false,
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS to true,
                        Settings.ACTION_SETTINGS to true,
                    ),
                launchByAction =
                    mapOf(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS to true,
                    ),
            )

        openAgentSettingsWithFallbacks(
            launcher = launcher,
            destinations = batteryOptimizationSettingsFallbackChain("com.rainng.androidctl"),
        )

        assertEquals(
            listOf(
                "resolve:${Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS}",
                "resolve:${Settings.ACTION_APPLICATION_DETAILS_SETTINGS}",
                "launch:${Settings.ACTION_APPLICATION_DETAILS_SETTINGS}",
            ),
            launcher.events,
        )
    }

    @Test
    fun batteryOptimizationFallbackMovesToAppInfoAfterLaunchFailure() {
        val launcher =
            RecordingAgentSettingsEntryLauncher(
                canResolveByAction =
                    mapOf(
                        Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS to true,
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS to true,
                    ),
                launchByAction =
                    mapOf(
                        Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS to false,
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS to true,
                    ),
            )

        openAgentSettingsWithFallbacks(
            launcher = launcher,
            destinations = batteryOptimizationSettingsFallbackChain("com.rainng.androidctl"),
        )

        assertEquals(
            listOf(
                "resolve:${Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS}",
                "launch:${Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS}",
                "resolve:${Settings.ACTION_APPLICATION_DETAILS_SETTINGS}",
                "launch:${Settings.ACTION_APPLICATION_DETAILS_SETTINGS}",
            ),
            launcher.events,
        )
    }

    @Test
    fun appInfoFallbackMovesToSystemSettingsWhenPrimaryIsUnresolvable() {
        val launcher =
            RecordingAgentSettingsEntryLauncher(
                canResolveByAction =
                    mapOf(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS to false,
                        Settings.ACTION_SETTINGS to true,
                    ),
                launchByAction =
                    mapOf(
                        Settings.ACTION_SETTINGS to true,
                    ),
            )

        openAgentSettingsWithFallbacks(
            launcher = launcher,
            destinations = appInfoSettingsFallbackChain("com.rainng.androidctl"),
        )

        assertEquals(
            listOf(
                "resolve:${Settings.ACTION_APPLICATION_DETAILS_SETTINGS}",
                "resolve:${Settings.ACTION_SETTINGS}",
                "launch:${Settings.ACTION_SETTINGS}",
            ),
            launcher.events,
        )
    }

    @Test
    fun appInfoFallbackMovesToSystemSettingsAfterLaunchFailure() {
        val launcher =
            RecordingAgentSettingsEntryLauncher(
                canResolveByAction =
                    mapOf(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS to true,
                        Settings.ACTION_SETTINGS to true,
                    ),
                launchByAction =
                    mapOf(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS to false,
                        Settings.ACTION_SETTINGS to true,
                    ),
            )

        openAgentSettingsWithFallbacks(
            launcher = launcher,
            destinations = appInfoSettingsFallbackChain("com.rainng.androidctl"),
        )

        assertEquals(
            listOf(
                "resolve:${Settings.ACTION_APPLICATION_DETAILS_SETTINGS}",
                "launch:${Settings.ACTION_APPLICATION_DETAILS_SETTINGS}",
                "resolve:${Settings.ACTION_SETTINGS}",
                "launch:${Settings.ACTION_SETTINGS}",
            ),
            launcher.events,
        )
    }

    @Test
    fun fallbackChainStopsAtTheFirstSuccessfulEntry() {
        val launcher =
            RecordingAgentSettingsEntryLauncher(
                canResolveByAction =
                    mapOf(
                        Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS to true,
                    ),
                launchByAction =
                    mapOf(
                        Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS to true,
                    ),
            )

        openAgentSettingsWithFallbacks(
            launcher = launcher,
            destinations = batteryOptimizationSettingsFallbackChain("com.rainng.androidctl"),
        )

        assertEquals(
            listOf(
                "resolve:${Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS}",
                "launch:${Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS}",
            ),
            launcher.events,
        )
    }

    private fun List<AgentSettingsDestination>.actions(): List<String> = map(AgentSettingsDestination::action)

    private class RecordingAgentSettingsEntryLauncher(
        private val canResolveByAction: Map<String, Boolean> = emptyMap(),
        private val launchByAction: Map<String, Boolean> = emptyMap(),
    ) : AgentSettingsEntryLauncher {
        val events = mutableListOf<String>()

        override fun canResolve(destination: AgentSettingsDestination): Boolean {
            events += "resolve:${destination.action}"
            return canResolveByAction.getOrDefault(destination.action, true)
        }

        override fun launch(destination: AgentSettingsDestination): Boolean {
            events += "launch:${destination.action}"
            return launchByAction.getOrDefault(destination.action, true)
        }
    }
}

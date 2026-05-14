package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Test

class AgentBackgroundReliabilityTest {
    @Test
    fun mapsIgnoredStatusToUiModel() {
        val model =
            BackgroundReliabilityState(
                batteryOptimizationStatus = BatteryOptimizationStatus.IGNORED,
            ).toBackgroundReliabilityUiModel()

        assertEquals(BatteryOptimizationStatus.IGNORED, model.batteryOptimizationStatus)
        assertEquals(R.string.status_battery_optimization_ignored, model.batteryOptimizationStatusRes)
        assertEquals(
            R.string.status_battery_optimization_ignored_detail,
            model.batteryOptimizationDetailRes,
        )
    }

    @Test
    fun mapsNotIgnoredStatusToUiModel() {
        val model =
            BackgroundReliabilityState(
                batteryOptimizationStatus = BatteryOptimizationStatus.NOT_IGNORED,
            ).toBackgroundReliabilityUiModel()

        assertEquals(BatteryOptimizationStatus.NOT_IGNORED, model.batteryOptimizationStatus)
        assertEquals(
            R.string.status_battery_optimization_not_ignored,
            model.batteryOptimizationStatusRes,
        )
    }

    @Test
    fun mapsUnknownStatusToUiModel() {
        val model =
            BackgroundReliabilityState(
                batteryOptimizationStatus = BatteryOptimizationStatus.UNKNOWN,
            ).toBackgroundReliabilityUiModel()

        assertEquals(BatteryOptimizationStatus.UNKNOWN, model.batteryOptimizationStatus)
        assertEquals(R.string.status_battery_optimization_unknown, model.batteryOptimizationStatusRes)
        assertEquals(
            R.string.status_battery_optimization_unknown_detail,
            model.batteryOptimizationDetailRes,
        )
    }

    @Test
    fun resolvesIgnoredWhenProbeReportsAllowlist() {
        val status =
            resolveBatteryOptimizationStatus(
                packageName = "com.rainng.androidctl",
                isIgnoringBatteryOptimizations = { true },
            )

        assertEquals(BatteryOptimizationStatus.IGNORED, status)
    }

    @Test
    fun resolvesNotIgnoredWhenProbeReportsRestriction() {
        val status =
            resolveBatteryOptimizationStatus(
                packageName = "com.rainng.androidctl",
                isIgnoringBatteryOptimizations = { false },
            )

        assertEquals(BatteryOptimizationStatus.NOT_IGNORED, status)
    }

    @Test
    fun resolvesUnknownForBlankPackageName() {
        val status =
            resolveBatteryOptimizationStatus(
                packageName = " ",
                isIgnoringBatteryOptimizations = { error("probe should not be used") },
            )

        assertEquals(BatteryOptimizationStatus.UNKNOWN, status)
    }

    @Test
    fun resolvesUnknownWhenProbeThrows() {
        val status =
            resolveBatteryOptimizationStatus(
                packageName = "com.rainng.androidctl",
                isIgnoringBatteryOptimizations = { throw IllegalStateException("system service unavailable") },
            )

        assertEquals(BatteryOptimizationStatus.UNKNOWN, status)
    }
}

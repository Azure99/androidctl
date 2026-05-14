package com.rainng.androidctl

import android.content.Context
import android.os.PowerManager
import androidx.annotation.StringRes

internal enum class BatteryOptimizationStatus {
    IGNORED,
    NOT_IGNORED,
    UNKNOWN,
}

internal data class BackgroundReliabilityState(
    val batteryOptimizationStatus: BatteryOptimizationStatus,
)

internal data class BackgroundReliabilityUiModel(
    val batteryOptimizationStatus: BatteryOptimizationStatus,
    @param:StringRes val batteryOptimizationStatusRes: Int,
    @param:StringRes val batteryOptimizationDetailRes: Int,
)

internal interface BackgroundReliabilityAccess {
    fun read(context: Context): BackgroundReliabilityState
}

internal object AndroidBackgroundReliabilityAccess : BackgroundReliabilityAccess {
    override fun read(context: Context): BackgroundReliabilityState =
        BackgroundReliabilityState(
            batteryOptimizationStatus =
                resolveBatteryOptimizationStatus(
                    packageName = context.packageName,
                    isIgnoringBatteryOptimizations = { packageName ->
                        checkNotNull(context.getSystemService(PowerManager::class.java)) {
                            "PowerManager unavailable"
                        }.isIgnoringBatteryOptimizations(packageName)
                    },
                ),
        )
}

internal fun BackgroundReliabilityState.toBackgroundReliabilityUiModel(): BackgroundReliabilityUiModel =
    BackgroundReliabilityUiModel(
        batteryOptimizationStatus = batteryOptimizationStatus,
        batteryOptimizationStatusRes = batteryOptimizationStatus.statusRes,
        batteryOptimizationDetailRes = batteryOptimizationStatus.detailRes,
    )

internal fun resolveBatteryOptimizationStatus(
    packageName: String,
    isIgnoringBatteryOptimizations: (String) -> Boolean,
): BatteryOptimizationStatus {
    if (packageName.isBlank()) {
        return BatteryOptimizationStatus.UNKNOWN
    }

    return runCatching { isIgnoringBatteryOptimizations(packageName) }
        .fold(
            onSuccess = { isIgnored ->
                if (isIgnored) {
                    BatteryOptimizationStatus.IGNORED
                } else {
                    BatteryOptimizationStatus.NOT_IGNORED
                }
            },
            onFailure = { BatteryOptimizationStatus.UNKNOWN },
        )
}

private val BatteryOptimizationStatus.statusRes: Int
    @StringRes
    get() =
        when (this) {
            BatteryOptimizationStatus.IGNORED -> R.string.status_battery_optimization_ignored
            BatteryOptimizationStatus.NOT_IGNORED ->
                R.string.status_battery_optimization_not_ignored
            BatteryOptimizationStatus.UNKNOWN -> R.string.status_battery_optimization_unknown
        }

private val BatteryOptimizationStatus.detailRes: Int
    @StringRes
    get() =
        when (this) {
            BatteryOptimizationStatus.IGNORED ->
                R.string.status_battery_optimization_ignored_detail
            BatteryOptimizationStatus.NOT_IGNORED ->
                R.string.status_battery_optimization_not_ignored_detail
            BatteryOptimizationStatus.UNKNOWN ->
                R.string.status_battery_optimization_unknown_detail
        }

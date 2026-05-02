package com.rainng.androidctl.agent.runtime

import android.view.accessibility.AccessibilityEvent

internal class ForegroundObservationStore(
    var foregroundHintUpdater: (ForegroundHintState, Int, String?, String?, Long) -> ForegroundHintState =
        { current, eventType, packageName, windowClassName, generation ->
            ForegroundHintTracker.update(
                current = current,
                eventType = eventType,
                packageName = packageName,
                windowClassName = windowClassName,
                generation = generation,
            )
        },
) : ForegroundObservationStateAccess {
    @Volatile
    private var foregroundHintState = ForegroundHintState()

    @Volatile
    private var foregroundGeneration = 0L

    @Synchronized
    fun recordObservedWindowState(
        eventType: Int,
        packageName: String?,
        windowClassName: String?,
    ) {
        val nextGeneration = nextForegroundGeneration(foregroundGeneration, eventType)
        foregroundHintState =
            foregroundHintUpdater(
                foregroundHintState,
                eventType,
                packageName,
                windowClassName,
                nextGeneration,
            )
        foregroundGeneration = nextGeneration
    }

    @Synchronized
    fun reset() {
        foregroundHintState = ForegroundHintState()
        foregroundGeneration = 0L
    }

    override fun foregroundHintState(): ForegroundHintState = foregroundHintState

    override fun foregroundGeneration(): Long = foregroundGeneration

    val currentForegroundHintState: ForegroundHintState
        get() = foregroundHintState()

    val currentForegroundGeneration: Long
        get() = foregroundGeneration()

    private fun nextForegroundGeneration(
        currentGeneration: Long,
        eventType: Int,
    ): Long =
        if (eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED ||
            eventType == AccessibilityEvent.TYPE_WINDOWS_CHANGED
        ) {
            currentGeneration + 1
        } else {
            currentGeneration
        }
}

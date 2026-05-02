package com.rainng.androidctl.agent.runtime

internal class ForegroundObservationManager(
    private val factsStore: RuntimeFactsStore,
    private val foregroundObservationStore: ForegroundObservationStore,
) {
    @Synchronized
    fun recordObservedWindowState(
        eventType: Int,
        packageName: String?,
        windowClassName: String?,
    ) {
        foregroundObservationStore.recordObservedWindowState(eventType, packageName, windowClassName)
        factsStore.update {
            it.copy(foreground = currentForegroundFacts())
        }
    }

    @Synchronized
    fun reset() {
        foregroundObservationStore.reset()
        factsStore.update { it.copy(foreground = ForegroundFacts()) }
    }

    private fun currentForegroundFacts(): ForegroundFacts {
        val generation = foregroundObservationStore.currentForegroundGeneration
        val hintState = foregroundObservationStore.currentForegroundHintState
        val packageName = hintState.fallbackPackageName(currentGeneration = generation, allowStale = true)
        val activityName = hintState.trustedActivityName(packageName = packageName, currentGeneration = generation)
        return ForegroundFacts(
            hintPackageName = packageName,
            hintActivityName = activityName,
            generation = generation,
        )
    }
}

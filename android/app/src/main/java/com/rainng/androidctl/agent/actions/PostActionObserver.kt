package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.runtime.ForegroundObservation
import com.rainng.androidctl.agent.runtime.ForegroundObservationProvider
import java.util.concurrent.TimeUnit

internal class PostActionObserver(
    private val observationProvider: ForegroundObservationProvider,
    private val observationPolicyFactory: (ActionRequest, ForegroundObservation, String?) -> PostActionObservationPolicy,
    private val nanoTimeProvider: () -> Long,
    private val sleepProvider: (Long) -> Unit,
) {
    fun observe(
        request: ActionRequest,
        initialObservation: ForegroundObservation,
    ): ForegroundObservation {
        val expectedPackageName = expectedPackageName(request)
        val observationPolicy =
            observationPolicyFactory(
                request,
                initialObservation,
                expectedPackageName,
            )
        var observedObservation = observationProvider.observe()

        if (observationPolicy.timeoutMs <= 0L ||
            isObservationSatisfied(
                observationPolicy = observationPolicy,
                observedObservation = observedObservation,
                initialObservation = initialObservation,
            )
        ) {
            return observedObservation
        }

        val deadline = nanoTimeProvider() + TimeUnit.MILLISECONDS.toNanos(observationPolicy.timeoutMs)
        while (nanoTimeProvider() < deadline) {
            sleepQuietly(observationPolicy.pollIntervalMs)
            observedObservation = observationProvider.observe()
            if (isObservationSatisfied(
                    observationPolicy = observationPolicy,
                    observedObservation = observedObservation,
                    initialObservation = initialObservation,
                )
            ) {
                break
            }
        }
        return observedObservation
    }

    private fun sleepQuietly(durationMs: Long) {
        if (durationMs <= 0L) {
            return
        }
        try {
            sleepProvider(durationMs)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }

    private fun isObservationSatisfied(
        observationPolicy: PostActionObservationPolicy,
        observedObservation: ForegroundObservation,
        initialObservation: ForegroundObservation,
    ): Boolean {
        val observedPackageName = observedObservation.state.packageName?.takeIf(String::isNotBlank)
        val initialPackageName = initialObservation.state.packageName?.takeIf(String::isNotBlank)

        if (observationPolicy.requiresGenerationAdvance &&
            observedObservation.generation <= initialObservation.generation
        ) {
            return false
        }

        return when (observationPolicy.packageRequirement) {
            PackageRequirement.NONE -> true
            PackageRequirement.EXPECTED -> observedPackageName == observationPolicy.expectedPackageName
            PackageRequirement.CHANGED_FROM_INITIAL -> {
                if (observedPackageName == null) {
                    false
                } else {
                    initialPackageName == null || observedPackageName != initialPackageName
                }
            }
        }
    }
}

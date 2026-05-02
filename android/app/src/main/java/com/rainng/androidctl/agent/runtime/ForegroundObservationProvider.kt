package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.os.PowerManager
import com.rainng.androidctl.agent.logging.AgentFallbackDiagnostics
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter

internal interface ForegroundObservationProvider {
    fun observe(): ForegroundObservation
}

internal class AccessibilityForegroundObservationProvider(
    private val foregroundWindowCandidatesProvider: () -> List<ForegroundWindowCandidate>,
    foregroundObservationStateAccess: ForegroundObservationStateAccess? = null,
    private val interactiveProvider: () -> Boolean,
    foregroundObservationStateAccessProvider: (() -> ForegroundObservationStateAccess)? = null,
    private val diagnosticReporter: RateLimitedDiagnosticReporter = AgentFallbackDiagnostics.reporter,
) : ForegroundObservationProvider {
    private val resolvedForegroundObservationStateAccessProvider: () -> ForegroundObservationStateAccess =
        foregroundObservationStateAccessProvider
            ?: { foregroundObservationStateAccess ?: AgentRuntimeBridge.foregroundObservationStateAccessRole }

    constructor(
        service: AccessibilityService,
        foregroundStateReader: AccessibilityForegroundStateReader = AccessibilityForegroundStateReader(service),
        foregroundObservationStateAccess: ForegroundObservationStateAccess? = null,
        interactiveProvider: () -> Boolean = {
            val powerManager = service.getSystemService(Context.POWER_SERVICE) as? PowerManager
            powerManager?.isInteractive ?: true
        },
        foregroundObservationStateAccessProvider: (() -> ForegroundObservationStateAccess)? = null,
        diagnosticReporter: RateLimitedDiagnosticReporter = AgentFallbackDiagnostics.reporter,
    ) : this(
        foregroundWindowCandidatesProvider = foregroundStateReader::readCandidates,
        foregroundObservationStateAccess = foregroundObservationStateAccess,
        interactiveProvider = interactiveProvider,
        foregroundObservationStateAccessProvider = foregroundObservationStateAccessProvider,
        diagnosticReporter = diagnosticReporter,
    )

    override fun observe(): ForegroundObservation {
        val foregroundObservationStateAccess = resolvedForegroundObservationStateAccessProvider()
        val generation = foregroundObservationStateAccess.foregroundGeneration()
        val interactive =
            runCatching(interactiveProvider).getOrElse { error ->
                diagnosticReporter.warn(
                    key = "foreground.interactive.fallback",
                    message = "foreground interactive state unavailable; using interactive=true",
                    throwable = error,
                )
                true
            }
        if (!interactive) {
            return ForegroundObservation(
                generation = generation,
                interactive = false,
            )
        }

        return ForegroundObservation(
            state =
                ForegroundWindowResolver.resolve(
                    windows = foregroundWindowCandidatesProvider(),
                    hintState = foregroundObservationStateAccess.foregroundHintState(),
                    generation = generation,
                    interactive = interactive,
                ),
            generation = generation,
            interactive = interactive,
        )
    }
}

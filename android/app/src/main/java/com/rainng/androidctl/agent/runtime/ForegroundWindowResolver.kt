package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.logging.AgentFallbackDiagnostics
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter

internal data class ForegroundWindowCandidate(
    val type: Int,
    val layer: Int,
    val packageName: String?,
    val active: Boolean,
    val focused: Boolean,
)

internal object ForegroundWindowResolver {
    fun resolve(
        windows: List<ForegroundWindowCandidate>,
        hintState: ForegroundHintState,
        generation: Long,
        interactive: Boolean,
    ): ObservedWindowState {
        if (!interactive) {
            return ObservedWindowState()
        }

        val applicationWindows =
            windows
                .asSequence()
                .filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
                .sortedWith(
                    compareByDescending<ForegroundWindowCandidate> { it.active }
                        .thenByDescending { it.focused }
                        .thenByDescending { it.layer },
                ).toList()

        val eligibleWindows =
            applicationWindows
                .asSequence()
                .filter { it.active || it.focused }
                .toList()

        val applicationWindowPackageName =
            applicationWindows
                .mapNotNull { it.packageName?.takeIf(String::isNotBlank) }
                .firstOrNull()

        val resolvedPackageName =
            eligibleWindows
                .mapNotNull { it.packageName?.takeIf(String::isNotBlank) }
                .firstOrNull()
                ?: applicationWindowPackageName
                ?: hintState
                    .fallbackPackageName(
                        currentGeneration = generation,
                        allowStale = applicationWindows.isEmpty(),
                    )

        val resolvedActivityName = hintState.trustedActivityName(resolvedPackageName, generation)

        return ObservedWindowState(
            packageName = resolvedPackageName,
            activityName = resolvedActivityName,
        )
    }
}

internal class AccessibilityForegroundStateReader(
    private val service: AccessibilityService,
    private val diagnosticReporter: RateLimitedDiagnosticReporter = AgentFallbackDiagnostics.reporter,
) {
    fun readCandidates(): List<ForegroundWindowCandidate> =
        runCatching {
            service.windows
                ?.mapNotNull { window ->
                    runCatching {
                        val root = window.root
                        try {
                            if (root == null && window.type != AccessibilityWindowInfo.TYPE_APPLICATION) {
                                return@runCatching null
                            }
                            foregroundWindowCandidate(
                                window = window,
                                packageName = root?.packageName?.toString(),
                                diagnosticReporter = diagnosticReporter,
                            )
                        } finally {
                            root?.recycle()
                        }
                    }.getOrElse { error ->
                        diagnosticReporter.warn(
                            key = "foreground.window.read.fallback",
                            message = "foreground window unavailable; dropping window candidate",
                            throwable = error,
                        )
                        null
                    }
                }.orEmpty()
        }.getOrElse { error ->
            diagnosticReporter.warn(
                key = "foreground.windows.read.fallback",
                message = "foreground windows unavailable; using empty candidate list",
                throwable = error,
            )
            emptyList()
        }
}

internal fun foregroundWindowCandidate(
    window: AccessibilityWindowInfo,
    packageName: String?,
    diagnosticReporter: RateLimitedDiagnosticReporter = AgentFallbackDiagnostics.reporter,
): ForegroundWindowCandidate =
    ForegroundWindowCandidate(
        type = window.type,
        layer = window.layer,
        packageName = packageName,
        active =
            runCatching { window.isActive }.getOrElse { error ->
                diagnosticReporter.warn(
                    key = "foreground.window.active.fallback",
                    message = "foreground window active state unavailable; using active=false",
                    throwable = error,
                )
                false
            },
        focused =
            runCatching { window.isFocused }.getOrElse { error ->
                diagnosticReporter.warn(
                    key = "foreground.window.focused.fallback",
                    message = "foreground window focus state unavailable; using focused=false",
                    throwable = error,
                )
                false
            },
    )

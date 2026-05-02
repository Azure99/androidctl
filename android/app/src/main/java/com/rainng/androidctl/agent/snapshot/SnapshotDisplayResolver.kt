package com.rainng.androidctl.agent.snapshot

import android.accessibilityservice.AccessibilityService
import android.view.Display
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.logging.AgentFallbackDiagnostics
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter

internal class SnapshotDisplayResolver(
    private val displayProvider: (Int) -> Display?,
    private val snapshotDisplayProvider: (Display) -> SnapshotDisplay?,
    private val diagnosticReporter: RateLimitedDiagnosticReporter = AgentFallbackDiagnostics.reporter,
) {
    fun resolve(
        service: AccessibilityService,
        windows: List<AccessibilityWindowInfo>,
    ): SnapshotDisplay {
        val candidateDisplayIds = resolveCandidateDisplayIds(windows)
        for (displayId in candidateDisplayIds) {
            val display =
                runCatching { displayProvider(displayId) }.getOrElse { error ->
                    diagnosticReporter.warn(
                        key = "snapshot.display.lookup.fallback",
                        message = "snapshot display lookup failed; trying next display source",
                        throwable = error,
                    )
                    null
                }
            if (display != null) {
                val snapshotDisplay =
                    runCatching { snapshotDisplayProvider(display) }.getOrElse { error ->
                        diagnosticReporter.warn(
                            key = "snapshot.display.snapshot.fallback",
                            message = "snapshot display metrics unavailable; trying next display source",
                            throwable = error,
                        )
                        null
                    }
                if (snapshotDisplay != null && isValidSnapshotDisplay(snapshotDisplay)) {
                    return snapshotDisplay
                }
            }
        }

        diagnosticReporter.warn(
            key = "snapshot.display.resource-metrics.fallback",
            message = "snapshot display unresolved; using resource display metrics",
        )
        val metrics = service.resources.displayMetrics
        return SnapshotDisplay(
            widthPx = metrics.widthPixels,
            heightPx = metrics.heightPixels,
            densityDpi = metrics.densityDpi,
            rotation = 0,
        )
    }

    private fun isValidSnapshotDisplay(snapshotDisplay: SnapshotDisplay): Boolean =
        snapshotDisplay.widthPx > 0 &&
            snapshotDisplay.heightPx > 0 &&
            snapshotDisplay.densityDpi > 0

    private fun resolveCandidateDisplayIds(windows: List<AccessibilityWindowInfo>): LinkedHashSet<Int> {
        val candidateDisplayIds = linkedSetOf<Int>()
        windows.forEach { window ->
            val displayId =
                runCatching { window.displayId }.getOrElse { error ->
                    diagnosticReporter.warn(
                        key = "snapshot.window.display-id.fallback",
                        message = "snapshot window display id unavailable; ignoring window display id",
                        throwable = error,
                    )
                    null
                }
            if (displayId != null && displayId != Display.INVALID_DISPLAY) {
                candidateDisplayIds += displayId
            }
        }
        candidateDisplayIds += Display.DEFAULT_DISPLAY
        return candidateDisplayIds
    }
}

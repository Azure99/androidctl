package com.rainng.androidctl.agent.events

import android.view.accessibility.AccessibilityEvent
import com.rainng.androidctl.agent.logging.AgentFallbackDiagnostics
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter

internal object DeviceEventObservationPolicy {
    private val COARSE_RAW_EVENT_TYPES =
        setOf(
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED,
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOWS_CHANGED,
            AccessibilityEvent.TYPE_VIEW_CLICKED,
            AccessibilityEvent.TYPE_VIEW_LONG_CLICKED,
            AccessibilityEvent.TYPE_VIEW_SELECTED,
            AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED,
            AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED,
            AccessibilityEvent.TYPE_VIEW_SCROLLED,
            AccessibilityEvent.TYPE_VIEW_FOCUSED,
            AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUSED,
            AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUS_CLEARED,
        )

    private val IME_STATE_REFRESH_EVENT_TYPES =
        setOf(
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOWS_CHANGED,
            AccessibilityEvent.TYPE_VIEW_FOCUSED,
            AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUSED,
            AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUS_CLEARED,
            AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED,
        )

    fun shouldObserveEvent(eventType: Int): Boolean = eventType in COARSE_RAW_EVENT_TYPES

    fun shouldRefreshImeState(eventType: Int): Boolean = eventType in IME_STATE_REFRESH_EVENT_TYPES
}

internal class ImeObservationController(
    private val diagnosticReporter: RateLimitedDiagnosticReporter = AgentFallbackDiagnostics.reporter,
) {
    private var lastImeState = ImeState(visible = false, windowId = null)

    @Synchronized
    fun reset() {
        lastImeState = ImeState(visible = false, windowId = null)
    }

    @Synchronized
    fun stateForEvent(
        eventType: Int,
        refreshImeState: () -> ImeState,
    ): ImeState {
        if (!DeviceEventObservationPolicy.shouldRefreshImeState(eventType)) {
            return lastImeState
        }

        val nextImeState =
            runCatching(refreshImeState).getOrElse { error ->
                diagnosticReporter.warn(
                    key = "events.ime.refresh.fallback",
                    message = "ime state refresh failed; reusing last state",
                    throwable = error,
                )
                lastImeState
            }
        lastImeState = nextImeState
        return nextImeState
    }
}

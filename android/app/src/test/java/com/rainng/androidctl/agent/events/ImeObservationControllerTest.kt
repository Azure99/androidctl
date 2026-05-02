package com.rainng.androidctl.agent.events

import android.view.accessibility.AccessibilityEvent
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ImeObservationControllerTest {
    @Test
    fun nonRefreshEventsReuseCachedImeState() {
        val controller = ImeObservationController()
        var refreshCalls = 0

        val visibleState =
            controller.stateForEvent(AccessibilityEvent.TYPE_VIEW_FOCUSED) {
                refreshCalls += 1
                ImeState(visible = true, windowId = "w7")
            }
        val reusedState =
            controller.stateForEvent(AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
                refreshCalls += 1
                ImeState(visible = false, windowId = null)
            }

        assertTrue(visibleState.visible)
        assertTrue(reusedState.visible)
        assertEquals("w7", reusedState.windowId)
        assertEquals(1, refreshCalls)
    }

    @Test
    fun refreshEventsUpdateCachedImeState() {
        val controller = ImeObservationController()

        controller.stateForEvent(AccessibilityEvent.TYPE_VIEW_FOCUSED) {
            ImeState(visible = true, windowId = "w7")
        }
        val hiddenState =
            controller.stateForEvent(AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
                ImeState(visible = false, windowId = null)
            }

        assertFalse(hiddenState.visible)
        assertEquals(null, hiddenState.windowId)
    }

    @Test
    fun refreshFailuresPreserveLastKnownImeState() {
        val controller = ImeObservationController(diagnosticReporter = testReporter())

        controller.stateForEvent(AccessibilityEvent.TYPE_VIEW_FOCUSED) {
            ImeState(visible = true, windowId = "w7")
        }
        val recoveredState =
            controller.stateForEvent(AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
                error("window lookup failed")
            }

        assertTrue(recoveredState.visible)
        assertEquals("w7", recoveredState.windowId)
    }

    @Test
    fun refreshFailuresWarnAndPreserveLastKnownImeState() {
        val warningMessages = mutableListOf<String>()
        val controller = ImeObservationController(diagnosticReporter = testReporter(warningMessages))

        controller.stateForEvent(AccessibilityEvent.TYPE_VIEW_FOCUSED) {
            ImeState(visible = true, windowId = "w7")
        }
        val recoveredState =
            controller.stateForEvent(AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
                error("window lookup failed")
            }

        assertTrue(recoveredState.visible)
        assertEquals("w7", recoveredState.windowId)
        assertEquals(
            listOf("ime state refresh failed; reusing last state"),
            warningMessages,
        )
    }

    @Test
    fun resetClearsCachedImeState() {
        val controller = ImeObservationController()

        controller.stateForEvent(AccessibilityEvent.TYPE_VIEW_FOCUSED) {
            ImeState(visible = true, windowId = "w7")
        }
        controller.reset()
        val resetState =
            controller.stateForEvent(AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
                ImeState(visible = false, windowId = null)
            }

        assertFalse(resetState.visible)
        assertEquals(null, resetState.windowId)
    }

    private fun testReporter(warningMessages: MutableList<String> = mutableListOf()): RateLimitedDiagnosticReporter =
        RateLimitedDiagnosticReporter(
            cooldownMs = 100L,
            clockMs = { 0L },
            warningLogger = { message, _ -> warningMessages += message },
        )
}

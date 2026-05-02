package com.rainng.androidctl.agent.events

import android.view.accessibility.AccessibilityEvent
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceEventObservationPolicyTest {
    @Test
    fun observesOnlyEventTypesConsumedByTheAggregator() {
        assertTrue(DeviceEventObservationPolicy.shouldObserveEvent(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED))
        assertTrue(DeviceEventObservationPolicy.shouldObserveEvent(AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED))
        assertFalse(DeviceEventObservationPolicy.shouldObserveEvent(AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED))
    }

    @Test
    fun refreshesImeOnlyForWindowAndFocusTransitions() {
        assertTrue(DeviceEventObservationPolicy.shouldRefreshImeState(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED))
        assertTrue(DeviceEventObservationPolicy.shouldRefreshImeState(AccessibilityEvent.TYPE_VIEW_FOCUSED))
        assertFalse(DeviceEventObservationPolicy.shouldRefreshImeState(AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED))
        assertFalse(DeviceEventObservationPolicy.shouldRefreshImeState(AccessibilityEvent.TYPE_VIEW_SCROLLED))
    }
}

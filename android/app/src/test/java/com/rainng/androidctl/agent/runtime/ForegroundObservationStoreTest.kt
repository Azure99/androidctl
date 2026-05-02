package com.rainng.androidctl.agent.runtime

import android.view.accessibility.AccessibilityEvent
import org.junit.Assert.assertEquals
import org.junit.Test

class ForegroundObservationStoreTest {
    @Test
    fun recordObservedWindowStateAdvancesGenerationOnlyForWindowEvents() {
        val store = ForegroundObservationStore()

        store.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.android.settings",
            windowClassName = "com.android.settings.Settings\$WifiSettingsActivity",
        )

        assertEquals("com.android.settings", store.currentForegroundHintState.fallbackPackageName)
        assertEquals(1L, store.currentForegroundGeneration)

        store.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_VIEW_FOCUSED,
            packageName = "com.android.settings",
            windowClassName = "android.widget.TextView",
        )

        assertEquals("com.android.settings", store.currentForegroundHintState.fallbackPackageName)
        assertEquals(1L, store.currentForegroundGeneration)

        store.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOWS_CHANGED,
            packageName = null,
            windowClassName = null,
        )

        assertEquals("com.android.settings", store.currentForegroundHintState.fallbackPackageName)
        assertEquals(2L, store.currentForegroundGeneration)
    }
}

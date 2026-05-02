package com.rainng.androidctl.agent.snapshot

import android.view.accessibility.AccessibilityWindowInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SnapshotWindowSelectionTest {
    @Test
    fun imeInfoUsesFullWindowSetEvenWhenPayloadExcludesSystemWindows() {
        val allWindows =
            listOf(
                SnapshotWindowDescriptor(
                    id = 1,
                    type = AccessibilityWindowInfo.TYPE_APPLICATION,
                ),
                SnapshotWindowDescriptor(
                    id = 7,
                    type = AccessibilityWindowInfo.TYPE_INPUT_METHOD,
                ),
            )

        val payloadWindows =
            SnapshotWindowSelection.payloadWindows(
                windows = allWindows,
                includeSystemWindows = false,
            )
        val ime = SnapshotWindowSelection.imeInfo(allWindows)

        assertEquals(1, payloadWindows.size)
        assertEquals(1, payloadWindows.single().id)
        assertEquals(AccessibilityWindowInfo.TYPE_APPLICATION, payloadWindows.single().type)
        assertTrue(ime.visible)
    }

    @Test
    fun imeInfoReturnsNotVisibleWhenNoInputMethodWindowExists() {
        val ime =
            SnapshotWindowSelection.imeInfo(
                listOf(
                    SnapshotWindowDescriptor(
                        id = 1,
                        type = AccessibilityWindowInfo.TYPE_APPLICATION,
                    ),
                ),
            )

        assertFalse(ime.visible)
        assertEquals(null, ime.windowId)
    }
}

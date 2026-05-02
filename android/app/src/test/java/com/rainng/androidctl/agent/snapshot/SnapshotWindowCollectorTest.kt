package com.rainng.androidctl.agent.snapshot

import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.WindowIds
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class SnapshotWindowCollectorTest {
    @Test
    fun selectWindowsPreservesDescriptorsForImeWhileFilteringPayloadWindows() {
        val collector = SnapshotWindowCollector()
        val selection =
            collector.selectWindows(
                allWindows =
                    listOf(
                        mockWindow(id = 1, type = AccessibilityWindowInfo.TYPE_APPLICATION),
                        mockWindow(id = 7, type = AccessibilityWindowInfo.TYPE_INPUT_METHOD),
                        mockWindow(id = 9, type = AccessibilityWindowInfo.TYPE_SYSTEM),
                    ),
                includeSystemWindows = false,
            )

        assertEquals(listOf(1), selection.payloadWindows.map { it.id })
        val ime = SnapshotWindowSelection.imeInfo(selection.descriptors)
        assertTrue(ime.visible)
        assertEquals(WindowIds.fromPlatformWindowId(7), ime.windowId)
    }

    private fun mockWindow(
        id: Int,
        type: Int,
    ): AccessibilityWindowInfo {
        val window = mock(AccessibilityWindowInfo::class.java)
        `when`(window.id).thenReturn(id)
        `when`(window.type).thenReturn(type)
        return window
    }
}

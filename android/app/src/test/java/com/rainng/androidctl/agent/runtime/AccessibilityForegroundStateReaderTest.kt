package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.verify
import org.mockito.Mockito.`when`

class AccessibilityForegroundStateReaderTest {
    @Test
    fun readCandidatesProducesCandidateAndRecyclesRootedApplicationWindow() {
        val root = mock(AccessibilityNodeInfo::class.java)
        `when`(root.packageName).thenReturn("com.android.settings")
        val reader =
            AccessibilityForegroundStateReader(
                serviceWithWindows(
                    mockForegroundWindow(
                        type = AccessibilityWindowInfo.TYPE_APPLICATION,
                        layer = 4,
                        root = root,
                    ),
                ),
            )

        val candidates = reader.readCandidates()

        assertEquals(1, candidates.size)
        assertEquals("com.android.settings", candidates.single().packageName)
        verify(root).recycle()
    }

    @Test
    fun readCandidatesKeepsRootlessApplicationWindowWithUnknownPackage() {
        val reader =
            AccessibilityForegroundStateReader(
                serviceWithWindows(
                    mockForegroundWindow(
                        type = AccessibilityWindowInfo.TYPE_APPLICATION,
                        layer = 3,
                        root = null,
                    ),
                ),
            )

        val candidates = reader.readCandidates()

        assertEquals(1, candidates.size)
        assertEquals(AccessibilityWindowInfo.TYPE_APPLICATION, candidates.single().type)
        assertEquals(3, candidates.single().layer)
        assertNull(candidates.single().packageName)
    }

    @Test
    fun readCandidatesSkipsRootlessNonApplicationWindow() {
        val reader =
            AccessibilityForegroundStateReader(
                serviceWithWindows(
                    mockForegroundWindow(
                        type = AccessibilityWindowInfo.TYPE_SYSTEM,
                        layer = 2,
                        root = null,
                        active = true,
                        focused = true,
                    ),
                ),
            )

        val candidates = reader.readCandidates()

        assertEquals(emptyList<ForegroundWindowCandidate>(), candidates)
    }

    @Test
    fun readCandidatesSkipsBrokenRootlessWindowWithoutDroppingOtherCandidates() {
        val healthyRoot = mock(AccessibilityNodeInfo::class.java)
        `when`(healthyRoot.packageName).thenReturn("com.android.settings")
        val healthyWindow =
            mockForegroundWindow(
                type = AccessibilityWindowInfo.TYPE_APPLICATION,
                layer = 4,
                root = healthyRoot,
            )
        val brokenWindow = mock(AccessibilityWindowInfo::class.java)
        `when`(brokenWindow.root).thenReturn(null)
        `when`(brokenWindow.type).thenThrow(IllegalStateException("stale window"))

        val reader =
            AccessibilityForegroundStateReader(
                service = serviceWithWindows(healthyWindow, brokenWindow),
                diagnosticReporter = testReporter(),
            )

        val candidates = reader.readCandidates()

        assertEquals(1, candidates.size)
        assertEquals("com.android.settings", candidates.single().packageName)
        verify(healthyRoot).recycle()
    }

    private fun serviceWithWindows(vararg windows: AccessibilityWindowInfo): AccessibilityService {
        val service = mock(AccessibilityService::class.java)
        `when`(service.windows).thenReturn(windows.toList())
        return service
    }

    private fun mockForegroundWindow(
        type: Int,
        layer: Int,
        root: AccessibilityNodeInfo?,
        active: Boolean = type == AccessibilityWindowInfo.TYPE_APPLICATION,
        focused: Boolean = type == AccessibilityWindowInfo.TYPE_APPLICATION,
    ): AccessibilityWindowInfo {
        val window = mock(AccessibilityWindowInfo::class.java)
        `when`(window.type).thenReturn(type)
        `when`(window.layer).thenReturn(layer)
        `when`(window.root).thenReturn(root)
        `when`(window.isActive).thenReturn(active)
        `when`(window.isFocused).thenReturn(focused)
        return window
    }

    private fun testReporter(): RateLimitedDiagnosticReporter =
        RateLimitedDiagnosticReporter(
            cooldownMs = 100L,
            clockMs = { 0L },
            warningLogger = { _, _ -> },
        )
}

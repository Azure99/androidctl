package com.rainng.androidctl.agent.snapshot

import android.accessibilityservice.AccessibilityService
import android.content.res.Resources
import android.util.DisplayMetrics
import android.view.Display
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter
import org.junit.Assert.assertEquals
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.never
import org.mockito.Mockito.verify
import org.mockito.Mockito.`when`

class SnapshotDisplayResolverTest {
    @Test
    fun fallsBackToResourceMetricsWhenWindowDisplaysAreInvalid() {
        val service = mock(AccessibilityService::class.java)
        val resources = mock(Resources::class.java)
        val metrics =
            DisplayMetrics().apply {
                widthPixels = 1080
                heightPixels = 2400
                densityDpi = 440
            }
        `when`(service.resources).thenReturn(resources)
        `when`(resources.displayMetrics).thenReturn(metrics)
        val resolver =
            SnapshotDisplayResolver(
                displayProvider = { null },
                snapshotDisplayProvider = { SnapshotDisplay(widthPx = 0, heightPx = 0, densityDpi = 0, rotation = 0) },
                diagnosticReporter = testReporter(),
            )

        val display = resolver.resolve(service, listOf(mockWindow(id = 7, displayId = 9)))

        assertEquals(1080, display.widthPx)
        assertEquals(2400, display.heightPx)
        assertEquals(440, display.densityDpi)
        assertEquals(0, display.rotation)
        verify(service, never()).getSystemService(Display::class.java)
    }

    @Test
    fun warnsWhenWindowDisplayIdFailsAndFallsBackToResourceMetrics() {
        val service = serviceWithMetrics(widthPx = 1080, heightPx = 2400, densityDpi = 440)
        val warningMessages = mutableListOf<String>()
        val window =
            mock(AccessibilityWindowInfo::class.java).also { window ->
                `when`(window.displayId).thenThrow(IllegalStateException("display id unavailable"))
            }
        val resolver =
            SnapshotDisplayResolver(
                displayProvider = { null },
                snapshotDisplayProvider = { null },
                diagnosticReporter = testReporter(warningMessages),
            )

        val display = resolver.resolve(service, listOf(window))

        assertEquals(1080, display.widthPx)
        assertEquals(
            listOf(
                "snapshot window display id unavailable; ignoring window display id",
                "snapshot display unresolved; using resource display metrics",
            ),
            warningMessages,
        )
    }

    @Test
    fun warnsWhenDisplayAndSnapshotLookupsFailBeforeResourceMetricsFallback() {
        val service = serviceWithMetrics(widthPx = 1080, heightPx = 2400, densityDpi = 440)
        val warningMessages = mutableListOf<String>()
        val platformDisplay = mock(Display::class.java)
        val resolver =
            SnapshotDisplayResolver(
                displayProvider = { displayId ->
                    if (displayId == 9) {
                        error("display unavailable")
                    }
                    platformDisplay
                },
                snapshotDisplayProvider = { error("snapshot unavailable") },
                diagnosticReporter = testReporter(warningMessages),
            )

        val display = resolver.resolve(service, listOf(mockWindow(id = 7, displayId = 9)))

        assertEquals(1080, display.widthPx)
        assertEquals(
            listOf(
                "snapshot display lookup failed; trying next display source",
                "snapshot display metrics unavailable; trying next display source",
                "snapshot display unresolved; using resource display metrics",
            ),
            warningMessages,
        )
    }

    @Test
    fun loggerFailureDoesNotBreakResourceMetricsFallback() {
        val service = serviceWithMetrics(widthPx = 1080, heightPx = 2400, densityDpi = 440)
        val resolver =
            SnapshotDisplayResolver(
                displayProvider = { null },
                snapshotDisplayProvider = { null },
                diagnosticReporter =
                    RateLimitedDiagnosticReporter(
                        warningLogger = { _, _ -> throw IllegalStateException("logger unavailable") },
                    ),
            )

        val display = resolver.resolve(service, listOf(mockWindow(id = 7, displayId = 9)))

        assertEquals(1080, display.widthPx)
        assertEquals(2400, display.heightPx)
        assertEquals(440, display.densityDpi)
        assertEquals(0, display.rotation)
    }

    private fun mockWindow(
        id: Int,
        displayId: Int,
    ): AccessibilityWindowInfo {
        val window = mock(AccessibilityWindowInfo::class.java)
        `when`(window.id).thenReturn(id)
        `when`(window.displayId).thenReturn(displayId)
        return window
    }

    private fun serviceWithMetrics(
        widthPx: Int,
        heightPx: Int,
        densityDpi: Int,
    ): AccessibilityService {
        val service = mock(AccessibilityService::class.java)
        val resources = mock(Resources::class.java)
        val metrics =
            DisplayMetrics().apply {
                widthPixels = widthPx
                heightPixels = heightPx
                this.densityDpi = densityDpi
            }
        `when`(service.resources).thenReturn(resources)
        `when`(resources.displayMetrics).thenReturn(metrics)
        return service
    }

    private fun testReporter(warningMessages: MutableList<String> = mutableListOf()): RateLimitedDiagnosticReporter =
        RateLimitedDiagnosticReporter(
            cooldownMs = 100L,
            clockMs = { 0L },
            warningLogger = { message, _ -> warningMessages += message },
        )
}

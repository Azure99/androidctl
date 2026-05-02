package com.rainng.androidctl.agent.runtime

import android.content.Context
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.mockito.Mockito.mock

class RuntimeProbesTest {
    @Test
    fun probeAccessibilityEnabledReturnsFalseAndLogsWhenProbeThrowsSecurityException() {
        val warnings = mutableListOf<String>()

        val enabled =
            probeAccessibilityEnabled(
                context = mock(Context::class.java),
                accessibilityServiceEnabledProbe = { throw SecurityException("no access") },
                warningLogger = warnings::add,
            )

        assertFalse(enabled)
        assertEquals(listOf("failed to probe accessibility state: no access"), warnings)
    }

    @Test
    fun probeServerRunningReturnsFalseAndLogsWhenProbeThrowsSecurityException() {
        val warnings = mutableListOf<String>()

        val running =
            probeServerRunning(
                context = mock(Context::class.java),
                serverRunningProbe = { throw SecurityException("not allowed") },
                warningLogger = warnings::add,
            )

        assertFalse(running)
        assertEquals(listOf("failed to probe server state: not allowed"), warnings)
    }
}

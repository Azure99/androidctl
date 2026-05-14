package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AgentStatusComponentsTest {
    @Test
    fun softWrapDisplayKeepsOriginalTextAndAddsBreakHintsForLongRuns() {
        val original = "request failed while parsing extremelylongdiagnosticsegment value"
        val transformed = original.softWrapDisplay(chunkSize = 12)

        assertTrue(transformed.contains('\u200B'))
        assertTrue(transformed.contains(' '))
        assertEquals(original, transformed.replace("\u200B", ""))
    }
}

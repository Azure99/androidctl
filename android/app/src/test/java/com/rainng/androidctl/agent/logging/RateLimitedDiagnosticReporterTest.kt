package com.rainng.androidctl.agent.logging

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

class RateLimitedDiagnosticReporterTest {
    @Test
    fun warnEmitsOncePerKeyWithinCooldownThenAgainAfterInterval() {
        var nowMs = 0L
        val records = mutableListOf<DiagnosticRecord>()
        val reporter =
            RateLimitedDiagnosticReporter(
                cooldownMs = 100L,
                clockMs = { nowMs },
                warningLogger = { message, throwable -> records += DiagnosticRecord(message, throwable) },
            )
        val firstError = IllegalStateException("first")
        val suppressedError = IllegalStateException("suppressed")
        val secondKeyError = IllegalStateException("second-key")
        val afterCooldownError = IllegalStateException("after-cooldown")

        reporter.warn("same-key", "first message", firstError)
        reporter.warn("same-key", "suppressed message", suppressedError)
        reporter.warn("other-key", "other message", secondKeyError)
        nowMs = 99L
        reporter.warn("same-key", "still suppressed", suppressedError)
        nowMs = 100L
        reporter.warn("same-key", "after cooldown", afterCooldownError)

        assertEquals(
            listOf("first message", "other message", "after cooldown"),
            records.map(DiagnosticRecord::message),
        )
        assertSame(firstError, records[0].throwable)
        assertSame(secondKeyError, records[1].throwable)
        assertSame(afterCooldownError, records[2].throwable)
    }

    private data class DiagnosticRecord(
        val message: String,
        val throwable: Throwable?,
    )
}

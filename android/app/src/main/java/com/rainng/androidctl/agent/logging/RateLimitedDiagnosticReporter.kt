package com.rainng.androidctl.agent.logging

internal typealias DiagnosticWarningLogger = (String, Throwable?) -> Unit

internal class RateLimitedDiagnosticReporter(
    private val cooldownMs: Long = DEFAULT_COOLDOWN_MS,
    private val clockMs: () -> Long = System::currentTimeMillis,
    private val warningLogger: DiagnosticWarningLogger = AgentLog::w,
) {
    private val lock = Any()
    private val nextAllowedMsByKey = mutableMapOf<String, Long>()

    init {
        require(cooldownMs > 0L) { "cooldownMs must be positive" }
    }

    fun warn(
        key: String,
        message: String,
        throwable: Throwable? = null,
    ) {
        val nowMs = clockMs()
        val shouldLog =
            synchronized(lock) {
                val nextAllowedMs = nextAllowedMsByKey[key]
                if (nextAllowedMs != null && nowMs < nextAllowedMs) {
                    false
                } else {
                    nextAllowedMsByKey[key] = nowMs + cooldownMs
                    true
                }
            }

        if (shouldLog) {
            try {
                warningLogger(message, throwable)
            } catch (_: RuntimeException) {
                // Diagnostics must never make fallback paths fatal in local JVM or device runtime.
            }
        }
    }

    private companion object {
        const val DEFAULT_COOLDOWN_MS = 60_000L
    }
}

internal object AgentFallbackDiagnostics {
    val reporter = RateLimitedDiagnosticReporter()
}

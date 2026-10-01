package com.rainng.androidctl

import android.os.SystemClock

internal class UiBudget(
    private val responseDeadline: Long,
    private val now: () -> Long = SystemClock::elapsedRealtime,
) {
    private val started = now()

    init {
        if (responseDeadline <= started) throw Rejected("timeout", "Request budget exhausted before UI execution")
    }

    // All platform work uses this earlier cutoff; the response and host blocks keep their reserve.
    var deadline = responseDeadline - minOf(COLD_RESERVE_MS, (responseDeadline - started) / COLD_FRACTION)
        private set

    fun reserveDelivery(bytes: Int?) {
        if (bytes == null) return
        // Known capture size replaces the cold estimate with finalization and per-block delivery time.
        val blocks = (bytes + Limits.CHUNK_BYTES - 1) / Limits.CHUNK_BYTES
        deadline = responseDeadline - FINALIZE_MS - HOST_MS - blocks * BLOCK_MS
    }

    fun responseRemaining(): Long =
        (responseDeadline - minOf(HOST_MS, (responseDeadline - started) / COLD_FRACTION) - now()).coerceAtLeast(0)

    fun remaining(): Long = (deadline - now()).coerceAtLeast(0)

    fun canSample(
        previousWork: Long,
        until: Long,
    ): Boolean = previousWork <= minOf(until, deadline) - now()

    fun check() {
        if (remaining() == 0L) throw Rejected("timeout", "Request budget exhausted")
    }

    fun pause(until: Long = deadline) {
        val wait = minOf(SAMPLE_MS, until - now(), remaining())
        if (wait > 0) SystemClock.sleep(wait)
    }

    companion object {
        private const val COLD_RESERVE_MS = 500L
        private const val COLD_FRACTION = 5L
        private const val FINALIZE_MS = 100L
        private const val HOST_MS = 200L
        private const val BLOCK_MS = 50L
        const val SAMPLE_MS = 250L
        const val MAX_MS = 60000L
    }
}

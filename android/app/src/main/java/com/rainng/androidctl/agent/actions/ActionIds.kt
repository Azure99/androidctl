package com.rainng.androidctl.agent.actions

import java.util.concurrent.atomic.AtomicLong

object ActionIds {
    private const val ACTION_SEQUENCE_WIDTH = 5
    private val sequence = AtomicLong(0L)

    fun nextActionId(): String = "act-${sequence.incrementAndGet().toString().padStart(ACTION_SEQUENCE_WIDTH, '0')}"
}

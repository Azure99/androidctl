package com.rainng.androidctl.agent.events

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.codec.JsonDecoder
import com.rainng.androidctl.agent.rpc.codec.JsonReader

internal object EventPollRequestCodec : JsonDecoder<EventPollRequest> {
    private const val MAX_EVENTS_LIMIT = 100

    override fun read(reader: JsonReader): EventPollRequest {
        val afterSeq =
            reader.optionalLong(
                key = "afterSeq",
                invalidMessage = "events.poll afterSeq must be an integer",
            ) ?: 0L
        val waitMs =
            reader.optionalLong(
                key = "waitMs",
                invalidMessage = "events.poll waitMs must be an integer",
            ) ?: RequestBudgets.DEFAULT_EVENTS_WAIT_MS
        val limit =
            reader.optionalInt(
                key = "limit",
                invalidMessage = "events.poll limit must be an integer",
            ) ?: RequestBudgets.DEFAULT_EVENTS_LIMIT

        if (afterSeq < 0L) {
            throw RequestValidationException("events.poll requires afterSeq >= 0")
        }
        if (waitMs < 0L) {
            throw RequestValidationException("events.poll requires waitMs >= 0")
        }
        if (waitMs > RequestBudgets.MAX_EVENTS_WAIT_MS) {
            throw RequestValidationException("events.poll requires waitMs <= ${RequestBudgets.MAX_EVENTS_WAIT_MS}")
        }
        if (limit < 1) {
            throw RequestValidationException("events.poll requires limit > 0")
        }

        return EventPollRequest(
            afterSeq = afterSeq,
            waitMs = waitMs,
            limit = limit.coerceAtMost(MAX_EVENTS_LIMIT),
        )
    }
}

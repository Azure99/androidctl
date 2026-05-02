package com.rainng.androidctl.agent.events

import com.rainng.androidctl.agent.rpc.codec.JsonEncoder
import com.rainng.androidctl.agent.rpc.codec.JsonWriter

internal object EventPollResultCodec : JsonEncoder<EventPollResult> {
    override fun write(
        writer: JsonWriter,
        value: EventPollResult,
    ) {
        writer.array("events") { eventsWriter ->
            value.events.forEach { event ->
                eventsWriter.objectElement { eventWriter ->
                    DeviceEventCodec.write(eventWriter, event)
                }
            }
        }
        writer.requiredLong("latestSeq", value.latestSeq)
        writer.requiredBoolean("needResync", value.needResync)
        writer.requiredBoolean("timedOut", value.timedOut)
    }
}

package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.rpc.codec.ForegroundContextFragmentCodec
import com.rainng.androidctl.agent.rpc.codec.JsonEncoder
import com.rainng.androidctl.agent.rpc.codec.JsonWriter

internal object ActionResultCodec : JsonEncoder<ActionResult> {
    override fun write(
        writer: JsonWriter,
        value: ActionResult,
    ) {
        writer.requiredString("actionId", value.actionId)
        writer.requiredString("status", value.status.wireName)
        writer.requiredLong("durationMs", value.durationMs)
        writer.objectField("resolvedTarget") { resolvedTarget ->
            ActionTargetCodec.write(resolvedTarget, value.resolvedTarget)
        }
        writer.objectField("observed") { observed ->
            ForegroundContextFragmentCodec.writeFields(observed, value.observed.packageName, value.observed.activityName)
        }
    }
}

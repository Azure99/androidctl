package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.rpc.codec.JsonEncoder
import com.rainng.androidctl.agent.rpc.codec.JsonWriter

internal object MetaResponseCodec : JsonEncoder<MetaResponse> {
    override fun write(
        writer: JsonWriter,
        value: MetaResponse,
    ) {
        writer.requiredString("service", value.service)
        writer.requiredString("version", value.version)
        writer.objectField("capabilities") { capabilities ->
            capabilities.requiredBoolean("supportsEventsPoll", value.capabilities.supportsEventsPoll)
            capabilities.requiredBoolean("supportsScreenshot", value.capabilities.supportsScreenshot)
            capabilities.array("actionKinds") { actionKinds ->
                value.capabilities.actionKinds.forEach(actionKinds::requiredStringValue)
            }
        }
    }
}

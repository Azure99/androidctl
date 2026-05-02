package com.rainng.androidctl.agent.screenshot

import com.rainng.androidctl.agent.rpc.codec.JsonEncoder
import com.rainng.androidctl.agent.rpc.codec.JsonWriter

internal object ScreenshotResponseCodec : JsonEncoder<ScreenshotResponse> {
    override fun write(
        writer: JsonWriter,
        value: ScreenshotResponse,
    ) {
        writer.requiredString("contentType", value.contentType)
        writer.requiredInt("widthPx", value.widthPx)
        writer.requiredInt("heightPx", value.heightPx)
        writer.requiredString("bodyBase64", value.bodyBase64)
    }
}

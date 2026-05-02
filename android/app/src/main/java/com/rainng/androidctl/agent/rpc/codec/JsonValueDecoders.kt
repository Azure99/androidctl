package com.rainng.androidctl.agent.rpc.codec

import com.rainng.androidctl.agent.errors.RequestValidationException
import org.json.JSONObject

internal object JsonValueDecoders {
    fun asString(
        raw: Any?,
        invalidMessage: String,
    ): String = raw as? String ?: throw RequestValidationException(invalidMessage)

    fun asBoolean(
        raw: Any?,
        invalidMessage: String,
    ): Boolean = raw as? Boolean ?: throw RequestValidationException(invalidMessage)

    fun asInt(
        raw: Any?,
        invalidMessage: String,
    ): Int = JsonStrictNumberDecoder.asInt(raw, invalidMessage)

    fun asLong(
        raw: Any?,
        invalidMessage: String,
    ): Long = JsonStrictNumberDecoder.asLong(raw, invalidMessage)

    fun asDouble(
        raw: Any?,
        invalidMessage: String,
    ): Double {
        val number = raw as? Number ?: throw RequestValidationException(invalidMessage)
        val value = number.toDouble()
        if (!value.isFinite()) {
            throw RequestValidationException(invalidMessage)
        }
        return value
    }

    fun asObject(
        raw: Any?,
        invalidMessage: String,
    ): JSONObject = raw as? JSONObject ?: throw RequestValidationException(invalidMessage)
}

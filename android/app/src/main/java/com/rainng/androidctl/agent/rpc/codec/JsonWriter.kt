package com.rainng.androidctl.agent.rpc.codec

import org.json.JSONArray
import org.json.JSONObject

@Suppress("TooManyFunctions")
internal class JsonWriter private constructor(
    private val target: Any,
) {
    companion object {
        fun objectWriter(): JsonWriter = JsonWriter(JSONObject())

        fun arrayWriter(): JsonWriter = JsonWriter(JSONArray())
    }

    fun requiredString(
        key: String,
        value: String,
    ): JsonWriter = putObjectField(key, value)

    fun nullableString(
        key: String,
        value: String?,
    ): JsonWriter = putObjectField(key, value ?: JSONObject.NULL)

    fun requiredBoolean(
        key: String,
        value: Boolean,
    ): JsonWriter = putObjectField(key, value)

    fun requiredInt(
        key: String,
        value: Int,
    ): JsonWriter = putObjectField(key, value)

    fun requiredLong(
        key: String,
        value: Long,
    ): JsonWriter = putObjectField(key, value)

    fun requiredDouble(
        key: String,
        value: Double,
    ): JsonWriter = putObjectField(key, value)

    fun objectField(
        key: String,
        block: (JsonWriter) -> Unit,
    ): JsonWriter =
        apply {
            val child = objectWriter()
            block(child)
            putObjectField(key, child.toJsonObject())
        }

    fun array(
        key: String,
        block: (JsonWriter) -> Unit,
    ): JsonWriter =
        apply {
            val child = arrayWriter()
            block(child)
            putObjectField(key, child.toJsonArray())
        }

    fun requiredStringValue(value: String): JsonWriter = putArrayValue(value)

    fun requiredIntValue(value: Int): JsonWriter = putArrayValue(value)

    fun objectElement(block: (JsonWriter) -> Unit): JsonWriter =
        apply {
            val child = objectWriter()
            block(child)
            putArrayValue(child.toJsonObject())
        }

    fun toJsonObject(): JSONObject = target as? JSONObject ?: error("JsonWriter is not writing an object")

    fun toJsonArray(): JSONArray = target as? JSONArray ?: error("JsonWriter is not writing an array")

    private fun putObjectField(
        key: String,
        value: Any,
    ): JsonWriter =
        apply {
            toJsonObject().put(key, value)
        }

    private fun putArrayValue(value: Any): JsonWriter =
        apply {
            toJsonArray().put(value)
        }
}

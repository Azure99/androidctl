package com.rainng.androidctl.agent.rpc.codec

import com.rainng.androidctl.agent.errors.RequestValidationException
import org.json.JSONObject

@Suppress("TooManyFunctions")
internal class JsonReader private constructor(
    value: Any?,
) {
    private val objectReader: JsonObjectReader? = (value as? JSONObject)?.let(::JsonObjectReader)
    private val structureReader: JsonStructureReader = JsonStructureReader(value)

    companion object {
        fun fromObject(value: JSONObject): JsonReader = JsonReader(value)
    }

    fun requiredString(
        key: String,
        missingMessage: String,
        invalidMessage: String,
    ): String = readRequiredField(key, missingMessage, invalidMessage, JsonValueDecoders::asString)

    fun optionalString(
        key: String,
        invalidMessage: String,
    ): String? = readOptionalField(key, invalidMessage, JsonValueDecoders::asString)

    fun optionalNullableString(
        key: String,
        invalidMessage: String,
    ): String? = readOptionalNullableField(key, invalidMessage, JsonValueDecoders::asString)

    fun requiredBoolean(
        key: String,
        missingMessage: String,
        invalidMessage: String,
    ): Boolean = readRequiredField(key, missingMessage, invalidMessage, JsonValueDecoders::asBoolean)

    fun requiredInt(
        key: String,
        missingMessage: String,
        invalidMessage: String,
    ): Int = readRequiredField(key, missingMessage, invalidMessage, JsonValueDecoders::asInt)

    fun optionalInt(
        key: String,
        invalidMessage: String,
    ): Int? = readOptionalField(key, invalidMessage, JsonValueDecoders::asInt)

    fun requiredLong(
        key: String,
        missingMessage: String,
        invalidMessage: String,
    ): Long = readRequiredField(key, missingMessage, invalidMessage, JsonValueDecoders::asLong)

    fun optionalLong(
        key: String,
        invalidMessage: String,
    ): Long? = readOptionalField(key, invalidMessage, JsonValueDecoders::asLong)

    fun requiredDouble(
        key: String,
        missingMessage: String,
        invalidMessage: String,
    ): Double = readRequiredField(key, missingMessage, invalidMessage, JsonValueDecoders::asDouble)

    fun optionalDouble(
        key: String,
        invalidMessage: String,
    ): Double? = readOptionalField(key, invalidMessage, JsonValueDecoders::asDouble)

    fun requiredObject(
        key: String,
        missingMessage: String,
        invalidMessage: String,
    ): JsonReader = JsonReader(readRequiredField(key, missingMessage, invalidMessage, JsonValueDecoders::asObject))

    fun optionalObject(
        key: String,
        invalidMessage: String,
    ): JsonReader? = readOptionalField(key, invalidMessage, JsonValueDecoders::asObject)?.let { JsonReader(it) }

    fun toJsonObject(): JSONObject = requireObject()

    private fun <T> readRequiredField(
        key: String,
        missingMessage: String,
        invalidMessage: String,
        mapper: (Any?, String) -> T,
    ): T = requireObjectReader().readRequiredField(key, missingMessage, invalidMessage, mapper)

    private fun <T> readOptionalField(
        key: String,
        invalidMessage: String,
        mapper: (Any?, String) -> T,
    ): T? = requireObjectReader().readOptionalField(key, invalidMessage, mapper)

    private fun <T> readOptionalNullableField(
        key: String,
        invalidMessage: String,
        mapper: (Any?, String) -> T,
    ): T? = requireObjectReader().readOptionalNullableField(key, invalidMessage, mapper)

    private fun requireObjectReader(): JsonObjectReader = objectReader ?: error("JsonReader is not reading an object")

    private fun requireObject(): JSONObject = structureReader.requireObject()
}

private class JsonObjectReader(
    value: JSONObject,
) {
    private val fieldReader: JsonFieldReader = JsonFieldReader(value)

    fun <T> readRequiredField(
        key: String,
        missingMessage: String,
        invalidMessage: String,
        mapper: (Any?, String) -> T,
    ): T {
        val field = fieldReader.readField(key)
        return when (field.state) {
            JsonFieldState.MISSING -> throw RequestValidationException(missingMessage)
            JsonFieldState.NULL -> throw RequestValidationException(invalidMessage)
            JsonFieldState.PRESENT -> mapper(field.value, invalidMessage)
        }
    }

    fun <T> readOptionalField(
        key: String,
        invalidMessage: String,
        mapper: (Any?, String) -> T,
    ): T? {
        val field = fieldReader.readField(key)
        return when (field.state) {
            JsonFieldState.MISSING -> null
            JsonFieldState.NULL -> throw RequestValidationException(invalidMessage)
            JsonFieldState.PRESENT -> mapper(field.value, invalidMessage)
        }
    }

    fun <T> readOptionalNullableField(
        key: String,
        invalidMessage: String,
        mapper: (Any?, String) -> T,
    ): T? {
        val field = fieldReader.readField(key)
        return when (field.state) {
            JsonFieldState.MISSING, JsonFieldState.NULL -> null
            JsonFieldState.PRESENT -> mapper(field.value, invalidMessage)
        }
    }
}

private class JsonFieldReader(
    private val value: JSONObject,
) {
    fun readField(key: String): JsonField =
        when {
            !value.has(key) -> JsonField(JsonFieldState.MISSING, null)
            value.isNull(key) -> JsonField(JsonFieldState.NULL, null)
            else -> JsonField(JsonFieldState.PRESENT, value.get(key))
        }
}

private class JsonStructureReader(
    private val value: Any?,
) {
    fun requireObject(): JSONObject = value as? JSONObject ?: error("JsonReader is not reading an object")
}

private data class JsonField(
    val state: JsonFieldState,
    val value: Any?,
)

private enum class JsonFieldState {
    MISSING,
    NULL,
    PRESENT,
}

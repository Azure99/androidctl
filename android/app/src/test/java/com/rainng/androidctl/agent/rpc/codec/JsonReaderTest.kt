package com.rainng.androidctl.agent.rpc.codec

import com.rainng.androidctl.agent.errors.RequestValidationException
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test
import java.math.BigDecimal
import java.math.BigInteger

class JsonReaderTest {
    @Test
    fun requiredIntReadsIntegralLiteral() {
        val reader = JsonReader.fromObject(JSONObject("""{"value":1}"""))

        assertEquals(
            1,
            reader.requiredInt(
                key = "value",
                missingMessage = "value is required",
                invalidMessage = "value must be an integer",
            ),
        )
    }

    @Test
    fun requiredIntRejectsDecimalLiteral() {
        val reader = JsonReader.fromObject(JSONObject("""{"value":1.0}"""))

        assertValidationError("value must be an integer") {
            reader.requiredInt(
                key = "value",
                missingMessage = "value is required",
                invalidMessage = "value must be an integer",
            )
        }
    }

    @Test
    fun requiredIntRejectsExponentLiteral() {
        val reader = JsonReader.fromObject(JSONObject("""{"value":1e3}"""))

        assertValidationError("value must be an integer") {
            reader.requiredInt(
                key = "value",
                missingMessage = "value is required",
                invalidMessage = "value must be an integer",
            )
        }
    }

    @Test
    fun requiredIntRejectsOutOfRangeObjectField() {
        val reader = JsonReader.fromObject(JSONObject().put("value", Int.MAX_VALUE.toLong() + 1L))

        assertValidationError("value must be an integer") {
            reader.requiredInt(
                key = "value",
                missingMessage = "value is required",
                invalidMessage = "value must be an integer",
            )
        }
    }

    @Test
    fun requiredLongValidatesObjectFieldNumericBounds() {
        val tooLarge =
            JsonReader.fromObject(JSONObject().put("value", BigInteger("9223372036854775808")))
        assertValidationError("value must be a long") {
            tooLarge.requiredLong(
                key = "value",
                missingMessage = "value is required",
                invalidMessage = "value must be a long",
            )
        }

        val inRange =
            JsonReader.fromObject(JSONObject().put("value", BigInteger("9223372036854775807")))
        assertEquals(
            9223372036854775807L,
            inRange.requiredLong(
                key = "value",
                missingMessage = "value is required",
                invalidMessage = "value must be a long",
            ),
        )

        val decimal = JsonReader.fromObject(JSONObject().put("value", BigDecimal("1")))
        assertValidationError("value must be a long") {
            decimal.requiredLong(
                key = "value",
                missingMessage = "value is required",
                invalidMessage = "value must be a long",
            )
        }
    }

    @Test
    fun optionalStringRejectsExplicitNull() {
        val reader = JsonReader.fromObject(JSONObject("""{"name":null}"""))

        assertValidationError("name must be a string") {
            reader.optionalString(
                key = "name",
                invalidMessage = "name must be a string",
            )
        }
    }

    @Test
    fun supportsNestedTraversalAcrossObjects() {
        val reader =
            JsonReader.fromObject(
                JSONObject(
                    """
                    {
                      "title":"alpha",
                      "enabled":true,
                      "count":3,
                      "child":{"id":7},
                      "nullableText":null
                    }
                    """.trimIndent(),
                ),
            )
        assertEquals("alpha", reader.requiredString("title", "title required", "title invalid"))
        assertEquals(true, reader.requiredBoolean("enabled", "enabled required", "enabled invalid"))
        assertEquals(3, reader.requiredInt("count", "count required", "count invalid"))
        assertNull(reader.optionalNullableString("missingText", "missingText invalid"))
        assertNull(
            reader.optionalNullableString(
                key = "nullableText",
                invalidMessage = "nullableText invalid",
            ),
        )

        val child = reader.requiredObject("child", "child required", "child invalid")
        assertEquals(7L, child.requiredLong("id", "id required", "id invalid"))
    }

    @Test
    fun optionalNullableStringAllowsMissingAndExplicitNullButRejectsWrongType() {
        assertNull(
            JsonReader
                .fromObject(JSONObject())
                .optionalNullableString(
                    key = "name",
                    invalidMessage = "name must be a string",
                ),
        )

        assertNull(
            JsonReader
                .fromObject(JSONObject("""{"name":null}"""))
                .optionalNullableString(
                    key = "name",
                    invalidMessage = "name must be a string",
                ),
        )

        assertValidationError("name must be a string") {
            JsonReader
                .fromObject(JSONObject("""{"name":1}"""))
                .optionalNullableString(
                    key = "name",
                    invalidMessage = "name must be a string",
                )
        }
    }

    @Test
    fun strictNumberDecoderPreservesUnknownNumberSemantics() {
        val integralNumber =
            object : Number() {
                override fun toByte(): Byte = 7

                override fun toDouble(): Double = 7.0

                override fun toFloat(): Float = 7.0f

                override fun toInt(): Int = 7

                override fun toLong(): Long = 7L

                override fun toShort(): Short = 7
            }

        val fractionalNumber =
            object : Number() {
                override fun toByte(): Byte = 1

                override fun toDouble(): Double = 1.5

                override fun toFloat(): Float = 1.5f

                override fun toInt(): Int = 1

                override fun toLong(): Long = 1L

                override fun toShort(): Short = 1
            }

        assertEquals(7L, JsonStrictNumberDecoder.asLong(integralNumber, "value must be a long"))
        assertValidationError("value must be a long") {
            JsonStrictNumberDecoder.asLong(fractionalNumber, "value must be a long")
        }
    }

    private fun assertValidationError(
        expectedMessage: String,
        block: () -> Unit,
    ) {
        try {
            block()
            fail("expected RequestValidationException")
        } catch (error: RequestValidationException) {
            assertEquals(expectedMessage, error.message)
        }
    }
}

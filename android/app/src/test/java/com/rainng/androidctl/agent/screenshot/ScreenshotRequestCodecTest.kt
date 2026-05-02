package com.rainng.androidctl.agent.screenshot

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.codec.JsonReader
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class ScreenshotRequestCodecTest {
    @Test
    fun readUsesDefaultsWhenFieldsAreMissing() {
        val request = ScreenshotRequestCodec.read(JsonReader.fromObject(JSONObject()))

        assertEquals(ScreenshotRequest(format = "png", scale = 1.0), request)
    }

    @Test
    fun readAcceptsCanonicalJpeg() {
        val request =
            ScreenshotRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"format":"jpeg","scale":0.5}"""),
                ),
            )

        assertEquals(ScreenshotRequest(format = "jpeg", scale = 0.5), request)
    }

    @Test
    fun readIgnoresUnknownTopLevelParamsFields() {
        val request =
            ScreenshotRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject(
                        """
                        {
                          "format": "jpeg",
                          "scale": 0.5,
                          "clientTag": "ignored",
                          "debug": {"trace": true},
                          "unusedNull": null
                        }
                        """.trimIndent(),
                    ),
                ),
            )

        assertEquals(ScreenshotRequest(format = "jpeg", scale = 0.5), request)
    }

    @Test
    fun readNormalizesCanonicalFormatsAfterCaseNormalization() {
        val uppercasePng =
            ScreenshotRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"format":"PNG","scale":1.0}"""),
                ),
            )
        val mixedCaseJpeg =
            ScreenshotRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"format":"JpEg","scale":0.5}"""),
                ),
            )

        assertEquals(ScreenshotRequest(format = "png", scale = 1.0), uppercasePng)
        assertEquals(ScreenshotRequest(format = "jpeg", scale = 0.5), mixedCaseJpeg)
    }

    @Test
    fun readRejectsJpgAlias() {
        assertValidationError("screenshot.capture requires format png or jpeg") {
            ScreenshotRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"format":"jpg","scale":0.5}"""),
                ),
            )
        }
    }

    @Test
    fun readRejectsScaleOutsideAllowedRange() {
        assertValidationError("screenshot.capture requires scale > 0") {
            ScreenshotRequestCodec.read(JsonReader.fromObject(JSONObject("""{"scale":0.0}""")))
        }
        assertValidationError("screenshot.capture requires scale <= ${RequestBudgets.MAX_SCREENSHOT_SCALE}") {
            ScreenshotRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"scale":${RequestBudgets.MAX_SCREENSHOT_SCALE + 0.5}}"""),
                ),
            )
        }
    }

    @Test
    fun readRejectsCoerciveTypes() {
        assertValidationError("screenshot.capture format must be a string") {
            ScreenshotRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"format":true}"""),
                ),
            )
        }
        assertValidationError("screenshot.capture scale must be a number") {
            ScreenshotRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"scale":"1.0"}"""),
                ),
            )
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

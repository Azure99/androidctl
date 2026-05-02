package com.rainng.androidctl.agent.events

import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.codec.JsonReader
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class EventPollRequestCodecTest {
    @Test
    fun readUsesDefaultsWhenFieldsAreMissing() {
        val request = EventPollRequestCodec.read(JsonReader.fromObject(JSONObject()))

        assertEquals(
            EventPollRequest(
                afterSeq = 0L,
                waitMs = 0L,
                limit = 20,
            ),
            request,
        )
    }

    @Test
    fun readClampsLimitToMax() {
        val request =
            EventPollRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"afterSeq":5,"waitMs":250,"limit":999}"""),
                ),
            )

        assertEquals(EventPollRequest(afterSeq = 5L, waitMs = 250L, limit = 100), request)
    }

    @Test
    fun readIgnoresUnknownTopLevelParamsFields() {
        val request =
            EventPollRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject(
                        """
                        {
                          "afterSeq": 5,
                          "waitMs": 250,
                          "limit": 12,
                          "clientTag": "ignored",
                          "debug": {"trace": true},
                          "unusedNull": null
                        }
                        """.trimIndent(),
                    ),
                ),
            )

        assertEquals(EventPollRequest(afterSeq = 5L, waitMs = 250L, limit = 12), request)
    }

    @Test
    fun readRejectsNegativeAfterSeq() {
        assertValidationError("events.poll requires afterSeq >= 0") {
            EventPollRequestCodec.read(JsonReader.fromObject(JSONObject("""{"afterSeq":-1}""")))
        }
    }

    @Test
    fun readRejectsWaitMsAboveBudget() {
        assertValidationError("events.poll requires waitMs <= 30000") {
            EventPollRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"waitMs":30001}"""),
                ),
            )
        }
    }

    @Test
    fun readRejectsNegativeWaitMs() {
        assertValidationError("events.poll requires waitMs >= 0") {
            EventPollRequestCodec.read(JsonReader.fromObject(JSONObject("""{"waitMs":-1}""")))
        }
    }

    @Test
    fun readRejectsNonPositiveLimit() {
        assertValidationError("events.poll requires limit > 0") {
            EventPollRequestCodec.read(JsonReader.fromObject(JSONObject("""{"limit":0}""")))
        }
    }

    @Test
    fun readRejectsCoerciveNumericTypes() {
        assertValidationError("events.poll waitMs must be an integer") {
            EventPollRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"waitMs":1.0}"""),
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

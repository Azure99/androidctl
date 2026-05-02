package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.errors.RequestValidationException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test

class RpcRequestParserTest {
    @Test
    fun parsesRequestEnvelope() {
        val envelope =
            RpcRequestParser.parse(
                """{"id":"req-1","method":"meta.get","params":{}}""",
            )

        assertEquals("req-1", envelope.id)
        assertEquals("meta.get", envelope.method)
    }

    @Test
    fun parsesRequestEnvelopeWithoutId() {
        val envelope =
            RpcRequestParser.parse(
                """{"method":"meta.get","params":{}}""",
            )

        assertNull(envelope.id)
        assertEquals("meta.get", envelope.method)
    }

    @Test
    fun defaultsMissingParamsToEmptyObject() {
        val envelope =
            RpcRequestParser.parse(
                """{"id":"req-2","method":"meta.get"}""",
            )

        assertEquals("req-2", envelope.id)
        assertEquals("meta.get", envelope.method)
        assertEquals(0, envelope.params.length())
    }

    @Test
    fun keepsObjectParamsPayload() {
        val envelope =
            RpcRequestParser.parse(
                """{"id":"req-2","method":"meta.get","params":{"x":1}}""",
            )

        assertEquals("req-2", envelope.id)
        assertEquals("meta.get", envelope.method)
        assertEquals(1, envelope.params.getInt("x"))
    }

    @Test
    fun rejectsInvalidJson() {
        assertParseError(
            rawBody = "not-json",
            expectedMessage = "request body must be valid JSON",
        )
    }

    @Test
    fun rejectsTopLevelArray() {
        assertParseError(
            rawBody = "[]",
            expectedMessage = "request body must be a JSON object",
        )
    }

    @Test
    fun rejectsInvalidId() {
        assertParseError(
            rawBody = """{"id":123,"method":"meta.get","params":{}}""",
            expectedMessage = "id must be a string",
        )
    }

    @Test
    fun rejectsMissingMethod() {
        assertParseError(
            rawBody = """{"id":"req-3","params":{}}""",
            expectedMessage = "method is required",
        )
    }

    @Test
    fun rejectsInvalidMethod() {
        assertParseError(
            rawBody = """{"id":"req-3","method":"","params":{}}""",
            expectedMessage = "method must be a non-blank string",
        )
    }

    @Test
    fun rejectsInvalidParams() {
        assertParseError(
            rawBody = """{"id":"req-4","method":"meta.get","params":[]}""",
            expectedMessage = "params must be a JSON object",
        )
    }

    private fun assertParseError(
        rawBody: String,
        expectedMessage: String,
    ) {
        try {
            RpcRequestParser.parse(rawBody)
            fail("expected RequestValidationException")
        } catch (error: RequestValidationException) {
            assertEquals(expectedMessage, error.message)
        }
    }
}

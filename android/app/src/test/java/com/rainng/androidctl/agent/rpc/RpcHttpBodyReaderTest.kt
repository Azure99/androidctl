package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream

class RpcHttpBodyReaderTest {
    @Test
    fun normalRequestBodyIsReadFromInputStream() {
        val result =
            newReader().read(
                FakeSession(
                    headers = mapOf("content-length" to """{"id":"req-1"}""".length.toString()),
                    body = """{"id":"req-1"}""",
                ),
            )

        assertNull(result.errorResponse)
        assertEquals("""{"id":"req-1"}""", result.body)
    }

    @Test
    fun malformedContentLengthReturnsBadRequest() {
        val response =
            newReader()
                .read(
                    FakeSession(
                        headers = mapOf("content-length" to "not-a-number"),
                        body = "{}",
                    ),
                ).errorResponse

        val details = assertInvalidRequest(response, NanoHTTPD.Response.Status.BAD_REQUEST)
        assertEquals("invalid_content_length", details.getString("reason"))
        assertEquals("not-a-number", details.getString("contentLength"))
    }

    @Test
    fun negativeContentLengthReturnsBadRequest() {
        val response =
            newReader()
                .read(
                    FakeSession(
                        headers = mapOf("content-length" to "-1"),
                        body = "{}",
                    ),
                ).errorResponse

        val details = assertInvalidRequest(response, NanoHTTPD.Response.Status.BAD_REQUEST)
        assertEquals("invalid_content_length", details.getString("reason"))
        assertEquals("-1", details.getString("contentLength"))
    }

    @Test
    fun oversizedContentLengthReturnsPayloadTooLargeWithoutReadingBody() {
        val inputStream = FailingInputStream()
        val response =
            newReader(maxBodyBytes = BODY_LIMIT)
                .read(
                    FakeSession(
                        headers = mapOf("content-length" to (BODY_LIMIT + 1).toString()),
                        inputStream = inputStream,
                    ),
                ).errorResponse

        val details = assertInvalidRequest(response, NanoHTTPD.Response.Status.PAYLOAD_TOO_LARGE)
        assertEquals("request_body_too_large", details.getString("reason"))
        assertEquals(BODY_LIMIT, details.getInt("max"))
        assertEquals((BODY_LIMIT + 1).toLong(), details.getLong("contentLength"))
        assertEquals(0, inputStream.readCalls)
    }

    @Test
    fun missingContentLengthReturnsBadRequestWithoutReadingBody() {
        val inputStream = FailingInputStream()
        val response =
            newReader(maxBodyBytes = BODY_LIMIT)
                .read(
                    FakeSession(
                        inputStream = inputStream,
                    ),
                ).errorResponse

        val details = assertInvalidRequest(response, NanoHTTPD.Response.Status.BAD_REQUEST)
        assertEquals("missing_content_length", details.getString("reason"))
        assertEquals(0, inputStream.readCalls)
    }

    @Test
    fun ioFailureReturnsInvalidRequestResponse() {
        val response =
            newReader()
                .read(
                    FakeSession(
                        headers = mapOf("content-length" to "1"),
                        inputStream =
                            object : InputStream() {
                                override fun read(): Int = throw IOException("broken stream")

                                override fun read(
                                    b: ByteArray,
                                    off: Int,
                                    len: Int,
                                ): Int = throw IOException("broken stream")
                            },
                    ),
                ).errorResponse

        assertInvalidRequest(response, NanoHTTPD.Response.Status.BAD_REQUEST)
    }

    private fun newReader(maxBodyBytes: Int = RequestBudgets.MAX_RPC_REQUEST_BODY_BYTES): RpcHttpBodyReader =
        RpcHttpBodyReader(
            onError = {},
            logError = { _, _ -> },
            maxBodyBytes = maxBodyBytes,
        )

    private fun assertInvalidRequest(
        response: NanoHTTPD.Response?,
        status: NanoHTTPD.Response.Status,
    ): JSONObject {
        assertTrue(response != null)
        assertEquals(status, response?.status)
        val payload = JSONObject(response!!.data.bufferedReader().readText())
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        return payload.getJSONObject("error").getJSONObject("details")
    }

    private class FakeSession(
        private val headers: Map<String, String> = emptyMap(),
        private val body: String = "",
        private val inputStream: InputStream = ByteArrayInputStream(body.toByteArray()),
    ) : NanoHTTPD.IHTTPSession {
        override fun execute() = Unit

        override fun getCookies(): NanoHTTPD.CookieHandler = throw UnsupportedOperationException()

        override fun getHeaders(): Map<String, String> = headers

        override fun getInputStream(): InputStream = inputStream

        override fun getMethod(): NanoHTTPD.Method = NanoHTTPD.Method.POST

        @Deprecated("Deprecated in NanoHTTPD")
        override fun getParms(): Map<String, String> = emptyMap()

        override fun getParameters(): Map<String, List<String>> = emptyMap()

        override fun getQueryParameterString(): String = ""

        override fun getUri(): String = "/rpc"

        override fun parseBody(files: MutableMap<String, String>) = error("parseBody must not be called")

        override fun getRemoteIpAddress(): String = "127.0.0.1"

        override fun getRemoteHostName(): String = "localhost"
    }

    private class FailingInputStream : InputStream() {
        var readCalls = 0

        override fun read(): Int {
            readCalls += 1
            throw AssertionError("input stream should not be read")
        }

        override fun read(
            b: ByteArray,
            off: Int,
            len: Int,
        ): Int {
            readCalls += 1
            throw AssertionError("input stream should not be read")
        }
    }

    private companion object {
        const val BODY_LIMIT = 8
    }
}

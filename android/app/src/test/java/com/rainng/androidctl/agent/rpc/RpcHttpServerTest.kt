package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.runtime.RuntimeReadiness
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream

class RpcHttpServerTest {
    @Test
    fun wrongPathReturnsNotFound() {
        val inputStream = FailingInputStream()
        val response =
            newServer().serve(
                FakeSession(
                    uri = "/not-rpc",
                    method = NanoHTTPD.Method.POST,
                    inputStream = inputStream,
                ),
            )

        assertEquals(NanoHTTPD.Response.Status.NOT_FOUND, response.status)
        val payload = readBody(response)
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals("/not-rpc", payload.getJSONObject("error").getJSONObject("details").getString("path"))
        assertEquals(0, inputStream.readCalls)
    }

    @Test
    fun nonPostReturnsMethodNotAllowed() {
        val inputStream = FailingInputStream()
        val response =
            newServer().serve(
                FakeSession(
                    uri = "/rpc",
                    method = NanoHTTPD.Method.GET,
                    inputStream = inputStream,
                ),
            )

        assertEquals(NanoHTTPD.Response.Status.METHOD_NOT_ALLOWED, response.status)
        val payload = readBody(response)
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals("GET", payload.getJSONObject("error").getJSONObject("details").getString("method"))
        assertEquals(0, inputStream.readCalls)
    }

    @Test
    fun bodyReadIOExceptionReturnsInvalidRequestAndReportsError() {
        val errors = mutableListOf<String>()
        val response =
            newServer(onError = errors::add).serve(
                FakeSession(
                    uri = "/rpc",
                    method = NanoHTTPD.Method.POST,
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
            )

        assertEquals(NanoHTTPD.Response.Status.BAD_REQUEST, response.status)
        assertEquals(
            "failed to parse RPC body: broken stream",
            errors.single(),
        )
        val payload = readBody(response)
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals(0, payload.getJSONObject("error").getJSONObject("details").length())
    }

    @Test
    fun oversizedContentLengthReturnsInvalidRequestEnvelope() {
        val inputStream = FailingInputStream()
        val response =
            newServer().serve(
                FakeSession(
                    uri = "/rpc",
                    method = NanoHTTPD.Method.POST,
                    headers = mapOf("content-length" to (RequestBudgets.MAX_RPC_REQUEST_BODY_BYTES + 1).toString()),
                    inputStream = inputStream,
                ),
            )

        assertEquals(NanoHTTPD.Response.Status.PAYLOAD_TOO_LARGE, response.status)
        val payload = readBody(response)
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        val details = payload.getJSONObject("error").getJSONObject("details")
        assertEquals("request_body_too_large", details.getString("reason"))
        assertEquals(0, inputStream.readCalls)
    }

    @Test
    fun missingContentLengthReturnsBadRequestWithoutReadingBody() {
        val inputStream = FailingInputStream()
        val requests = mutableListOf<String>()
        val response =
            newServer(onRequest = requests::add).serve(
                FakeSession(
                    uri = "/rpc",
                    method = NanoHTTPD.Method.POST,
                    autoContentLength = false,
                    inputStream = inputStream,
                ),
            )

        assertEquals(NanoHTTPD.Response.Status.BAD_REQUEST, response.status)
        val payload = readBody(response)
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        val details = payload.getJSONObject("error").getJSONObject("details")
        assertEquals("missing_content_length", details.getString("reason"))
        assertEquals(0, inputStream.readCalls)
        assertTrue(requests.isEmpty())
    }

    @Test
    fun validPostRpcDelegatesToHandlerAndRecordsRequest() {
        val requests = mutableListOf<String>()
        val response =
            newServer(onRequest = requests::add).serve(
                FakeSession(
                    uri = "/rpc",
                    method = NanoHTTPD.Method.POST,
                    headers = mapOf("authorization" to "Bearer device-token"),
                    body = """{"id":"req-1","method":"meta.get","params":{}}""",
                ),
            )

        val payload = readBody(response)
        assertEquals(NanoHTTPD.Response.Status.OK, response.status)
        assertTrue(payload.getBoolean("ok"))
        assertEquals(listOf("POST /rpc"), requests)
        assertEquals("androidctl-device-agent", payload.getJSONObject("result").getString("service"))
    }

    @Test
    fun numericMethodReturnsInvalidRequestEnvelope() {
        val response =
            newServer().serve(
                FakeSession(
                    uri = "/rpc",
                    method = NanoHTTPD.Method.POST,
                    headers = mapOf("authorization" to "Bearer device-token"),
                    body = """{"id":"req-invalid-method","method":123,"params":{}}""",
                ),
            )

        val payload = readBody(response)
        assertEquals(NanoHTTPD.Response.Status.OK, response.status)
        assertFalse(payload.getBoolean("ok"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals("req-invalid-method", payload.getString("id"))
    }

    @Test
    fun arrayParamsReturnsInvalidRequestEnvelope() {
        val response =
            newServer().serve(
                FakeSession(
                    uri = "/rpc",
                    method = NanoHTTPD.Method.POST,
                    headers = mapOf("authorization" to "Bearer device-token"),
                    body = """{"id":"req-invalid-params","method":"meta.get","params":[]}""",
                ),
            )

        val payload = readBody(response)
        assertEquals(NanoHTTPD.Response.Status.OK, response.status)
        assertFalse(payload.getBoolean("ok"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals("req-invalid-params", payload.getString("id"))
    }

    @Test
    fun quiescingServerReturnsStoppingEnvelopeForNewRequests() {
        val server = newServer()
        server.beginShutdown()

        val response =
            server.serve(
                FakeSession(
                    uri = "/rpc",
                    method = NanoHTTPD.Method.POST,
                    headers = mapOf("authorization" to "Bearer device-token"),
                    body = """{"id":"req-1","method":"meta.get","params":{}}""",
                ),
            )

        val payload = readBody(response)
        assertEquals(NanoHTTPD.Response.Status.OK, response.status)
        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INTERNAL_ERROR", payload.getJSONObject("error").getString("code"))
        assertEquals("server is stopping", payload.getJSONObject("error").getString("message"))
        assertEquals(true, payload.getJSONObject("error").getBoolean("retryable"))
    }

    @Test
    fun quiescingServerReturnsStoppingEnvelopeForRepeatedRequests() {
        val requests = mutableListOf<String>()
        val server = newServer(onRequest = requests::add)
        server.beginShutdown()

        repeat(5) { index ->
            val response =
                server.serve(
                    FakeSession(
                        uri = "/rpc",
                        method = NanoHTTPD.Method.POST,
                        headers = mapOf("authorization" to "Bearer device-token"),
                        body = """{"id":"req-$index","method":"meta.get","params":{}}""",
                    ),
                )

            val rawBody = response.data.bufferedReader().use { it.readText() }
            assertTrue("request $index should not return a blank body", rawBody.isNotBlank())
            val payload = JSONObject(rawBody)
            assertEquals(NanoHTTPD.Response.Status.OK, response.status)
            assertEquals(false, payload.getBoolean("ok"))
            assertEquals("INTERNAL_ERROR", payload.getJSONObject("error").getString("code"))
            assertEquals("server is stopping", payload.getJSONObject("error").getString("message"))
            assertEquals(true, payload.getJSONObject("error").getBoolean("retryable"))
        }

        assertTrue("quiescing requests should not be recorded as handled RPCs", requests.isEmpty())
    }

    @Test
    fun unexpectedHandlerFailureStillReturnsJsonEnvelope() {
        val response =
            newServer(
                requestHandler =
                    object : RpcRequestDelegate {
                        override fun handle(
                            headers: Map<String, String>,
                            rawBody: String,
                        ): String = error("boom")

                        override fun shutdown(force: Boolean) = Unit
                    },
            ).serve(
                FakeSession(
                    uri = "/rpc",
                    method = NanoHTTPD.Method.POST,
                    headers = mapOf("authorization" to "Bearer device-token"),
                    body = """{"id":"req-1","method":"meta.get","params":{}}""",
                ),
            )

        val payload = readBody(response)
        assertEquals(NanoHTTPD.Response.Status.OK, response.status)
        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INTERNAL_ERROR", payload.getJSONObject("error").getString("code"))
        assertEquals("boom", payload.getJSONObject("error").getString("message"))
    }

    @Test(expected = AssertionError::class)
    fun errorSubtypesAreNotConvertedIntoRpcEnvelopes() {
        newServer(
            requestHandler =
                object : RpcRequestDelegate {
                    override fun handle(
                        headers: Map<String, String>,
                        rawBody: String,
                    ): String = throw AssertionError("fatal")

                    override fun shutdown(force: Boolean) = Unit
                },
        ).serve(
            FakeSession(
                uri = "/rpc",
                method = NanoHTTPD.Method.POST,
                headers = mapOf("authorization" to "Bearer device-token"),
                body = """{"id":"req-1","method":"meta.get","params":{}}""",
            ),
        )
    }

    @Test
    fun gracefulFinishShutdownDoesNotForceRequestHandler() {
        val requestHandler = FakeRequestHandler()
        val server = newServer(requestHandler = requestHandler)

        server.beginShutdown()
        assertTrue(server.awaitQuiescence(timeoutMs = 10L))
        server.finishShutdown(force = false)

        assertEquals(listOf(false), requestHandler.shutdownForces)
    }

    @Test
    fun forcedFinishShutdownForcesRequestHandler() {
        val requestHandler = FakeRequestHandler()
        val server = newServer(requestHandler = requestHandler)

        server.beginShutdown()
        assertTrue(server.awaitQuiescence(timeoutMs = 10L))
        server.finishShutdown(force = true)

        assertEquals(listOf(true), requestHandler.shutdownForces)
    }

    private fun newServer(
        onRequest: (String) -> Unit = {},
        onError: (String) -> Unit = {},
        requestHandler: RpcRequestDelegate = newRequestHandler(),
    ): RpcHttpServer =
        RpcHttpServer(
            hostname = "127.0.0.1",
            port = 17171,
            callbacks =
                RpcHttpServerCallbacks(
                    onRequest = onRequest,
                    onError = onError,
                    logError = { _, _ -> },
                ),
            pipeline =
                RpcHttpServerPipeline(
                    admissionGate = RpcRequestAdmissionGate(),
                    validator = RpcHttpRequestValidator(),
                    bodyReader = RpcHttpBodyReader(onError = onError, logError = { _, _ -> }),
                    dispatchBoundary = RpcDispatchBoundary(requestHandler),
                    errorResponder = RpcHttpErrorResponder(),
                ),
        )

    private fun readBody(response: NanoHTTPD.Response): JSONObject =
        response.data.bufferedReader().use { reader ->
            JSONObject(reader.readText())
        }

    private fun newRequestHandler(): RpcRequestHandler {
        val methodExecutor = RpcRequestHandler.newMethodExecutor()
        return RpcRequestHandler(
            authorizationGate =
                RpcAuthorizationGate(
                    expectedTokenProvider = { "device-token" },
                    readinessProvider = { RuntimeReadiness(false, false) },
                ),
            dispatcher =
                RpcDispatcher(
                    runtimeAccess =
                        object : com.rainng.androidctl.agent.runtime.RuntimeAccess {
                            override fun readiness(): RuntimeReadiness = RuntimeReadiness(false, false)

                            override fun currentDeviceToken(): String = "device-token"

                            override fun applicationContext() = null

                            override fun currentAccessibilityService() = null
                        },
                    methodCatalog =
                        RpcMethodCatalog(
                            listOf(
                                MetaGetMethod(versionProvider = { "1.0.0" }),
                            ),
                        ),
                    executionRunner = RpcExecutionRunner(methodExecutor),
                ),
            methodExecutor = methodExecutor,
        )
    }

    private class FakeSession(
        private val uri: String,
        private val method: NanoHTTPD.Method,
        private val headers: Map<String, String> = emptyMap(),
        private val body: String = "",
        private val inputStream: InputStream = ByteArrayInputStream(body.toByteArray()),
        private val autoContentLength: Boolean = true,
    ) : NanoHTTPD.IHTTPSession {
        override fun execute() = Unit

        override fun getCookies(): NanoHTTPD.CookieHandler = throw UnsupportedOperationException("cookies are not used by RpcHttpServer")

        override fun getHeaders(): Map<String, String> =
            if (autoContentLength && body.isNotEmpty() && !hasContentLength(headers)) {
                headers + (CONTENT_LENGTH_HEADER to body.toByteArray(Charsets.UTF_8).size.toString())
            } else {
                headers
            }

        override fun getInputStream(): InputStream = inputStream

        override fun getMethod(): NanoHTTPD.Method = method

        @Deprecated("Deprecated in NanoHTTPD")
        override fun getParms(): Map<String, String> = emptyMap()

        override fun getParameters(): Map<String, List<String>> = emptyMap()

        override fun getQueryParameterString(): String = ""

        override fun getUri(): String = uri

        override fun parseBody(files: MutableMap<String, String>) = error("parseBody must not be called")

        override fun getRemoteIpAddress(): String = "127.0.0.1"

        override fun getRemoteHostName(): String = "localhost"

        private fun hasContentLength(headers: Map<String, String>): Boolean =
            headers.keys.any { it.equals(CONTENT_LENGTH_HEADER, ignoreCase = true) }
    }

    private class FakeRequestHandler : RpcRequestDelegate {
        val shutdownForces = mutableListOf<Boolean>()

        override fun handle(
            headers: Map<String, String>,
            rawBody: String,
        ): String = RpcEnvelope.success(id = "req-1", result = JSONObject().put("service", "fake"))

        override fun shutdown(force: Boolean) {
            shutdownForces += force
        }
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
        const val CONTENT_LENGTH_HEADER = "content-length"
    }
}

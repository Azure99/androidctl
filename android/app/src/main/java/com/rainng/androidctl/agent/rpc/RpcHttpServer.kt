package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.errors.RpcErrorCode
import fi.iki.elonen.NanoHTTPD

internal class RpcHttpServer(
    hostname: String,
    port: Int,
    private val callbacks: RpcHttpServerCallbacks,
    private val pipeline: RpcHttpServerPipeline,
) : NanoHTTPD(hostname, port) {
    override fun stop() {
        try {
            pipeline.admissionGate.beginShutdown()
            pipeline.dispatchBoundary.shutdown(force = true)
            super.stop()
        } finally {
            pipeline.admissionGate.finishShutdown()
        }
    }

    @Suppress("TooGenericExceptionCaught")
    override fun serve(session: IHTTPSession): Response =
        pipeline.admissionGate.enterOrReject(::stoppingResponse)?.let { return it } ?: try {
            serveAcceptedRequest(session)
        } catch (error: Exception) {
            pipeline.errorResponder.unexpected(error = error, onError = callbacks.onError, logError = callbacks.logError)
        } finally {
            pipeline.admissionGate.leave()
        }

    fun beginShutdown() {
        pipeline.admissionGate.beginShutdown()
    }

    fun awaitQuiescence(timeoutMs: Long): Boolean = pipeline.admissionGate.awaitQuiescence(timeoutMs)

    fun finishShutdown(force: Boolean) {
        try {
            pipeline.dispatchBoundary.shutdown(force = force)
        } finally {
            super.stop()
            pipeline.admissionGate.finishShutdown()
        }
    }

    private fun serveAcceptedRequest(session: IHTTPSession): Response {
        val requestBody = prepareRequest(session)
        return requestBody.errorResponse ?: dispatchRequest(session, requestBody.body.orEmpty())
    }

    private fun prepareRequest(session: IHTTPSession): RpcHttpBodyReadResult =
        pipeline.validator.validate(session.uri, session.method)?.let { validationError ->
            RpcHttpBodyReadResult(errorResponse = validationError)
        } ?: readRequestBody(session)

    private fun dispatchRequest(
        session: IHTTPSession,
        requestBody: String,
    ): Response {
        callbacks.onRequest("POST ${session.uri}")
        val responseBody = pipeline.dispatchBoundary.dispatch(session.headers, requestBody)
        return jsonResponse(Response.Status.OK, responseBody)
    }

    private fun readRequestBody(session: IHTTPSession): RpcHttpBodyReadResult = pipeline.bodyReader.read(session)

    private fun stoppingResponse(): Response =
        jsonResponse(
            status = Response.Status.OK,
            body =
                RpcEnvelope.error(
                    id = null,
                    code = RpcErrorCode.INTERNAL_ERROR,
                    message = "server is stopping",
                    retryable = true,
                ),
        )
}

internal data class RpcHttpServerCallbacks(
    val onRequest: (String) -> Unit,
    val onError: (String) -> Unit,
    val logError: (String, Throwable?) -> Unit,
)

internal data class RpcHttpServerPipeline(
    val admissionGate: RpcRequestAdmissionGate,
    val validator: RpcHttpRequestValidator,
    val bodyReader: RpcHttpBodyReader,
    val dispatchBoundary: RpcDispatchBoundary,
    val errorResponder: RpcHttpErrorResponder,
)

internal fun jsonResponse(
    status: NanoHTTPD.Response.Status,
    body: String,
): NanoHTTPD.Response = NanoHTTPD.newFixedLengthResponse(status, "application/json; charset=utf-8", body)

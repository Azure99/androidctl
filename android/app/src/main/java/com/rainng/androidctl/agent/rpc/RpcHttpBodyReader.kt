package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RpcErrorCode
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream

internal data class RpcHttpBodyReadResult(
    val body: String? = null,
    val errorResponse: NanoHTTPD.Response? = null,
)

internal class RpcHttpBodyReader(
    private val onError: (String) -> Unit,
    private val logError: (String, Throwable?) -> Unit,
    private val maxBodyBytes: Int = RequestBudgets.MAX_RPC_REQUEST_BODY_BYTES,
) {
    fun read(session: NanoHTTPD.IHTTPSession): RpcHttpBodyReadResult =
        try {
            val contentLengthResult = parseContentLength(session.headers)
            contentLengthResult.errorResponse?.let { return RpcHttpBodyReadResult(errorResponse = it) }

            val contentLength = requireNotNull(contentLengthResult.value)
            if (contentLength > maxBodyBytes.toLong()) {
                return bodyTooLargeResponse(contentLength)
            }

            RpcHttpBodyReadResult(body = readLimitedBody(session.inputStream, contentLength).trim())
        } catch (io: IOException) {
            onError("failed to parse RPC body: ${io.message}")
            logError("failed to parse RPC body", io)
            unableToReadBodyResponse()
        } catch (tooLarge: RpcRequestBodyTooLargeException) {
            bodyTooLargeResponse(tooLarge.contentLength)
        }

    private fun parseContentLength(headers: Map<String, String>): ContentLengthParseResult {
        val rawValue =
            contentLengthHeader(headers)
                ?: return ContentLengthParseResult(errorResponse = missingContentLengthResponse())
        val parsed = rawValue.toLongOrNull()
        if (parsed == null || parsed < 0L) {
            return ContentLengthParseResult(errorResponse = invalidContentLengthResponse(rawValue))
        }
        return ContentLengthParseResult(value = parsed)
    }

    private fun contentLengthHeader(headers: Map<String, String>): String? =
        headers.entries.firstOrNull { it.key.equals(CONTENT_LENGTH_HEADER, ignoreCase = true) }?.value

    private fun invalidContentLengthResponse(rawValue: String): NanoHTTPD.Response =
        errorResponse(
            status = NanoHTTPD.Response.Status.BAD_REQUEST,
            message = "invalid Content-Length",
            details =
                JSONObject()
                    .put("reason", "invalid_content_length")
                    .put("contentLength", rawValue),
        )

    private fun missingContentLengthResponse(): NanoHTTPD.Response =
        errorResponse(
            status = NanoHTTPD.Response.Status.BAD_REQUEST,
            message = "missing Content-Length",
            details = JSONObject().put("reason", "missing_content_length"),
        )

    private fun readLimitedBody(
        inputStream: InputStream,
        contentLength: Long,
    ): String {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(READ_BUFFER_BYTES)
        var totalBytes = 0L

        while (totalBytes < contentLength) {
            val bytesToRead = minOf(buffer.size.toLong(), contentLength - totalBytes).toInt()
            val read = inputStream.read(buffer, 0, bytesToRead)
            if (read == END_OF_STREAM) {
                break
            }
            totalBytes += read.toLong()
            if (totalBytes > maxBodyBytes) {
                throw RpcRequestBodyTooLargeException(contentLength)
            }
            output.write(buffer, 0, read)
        }

        return output.toString(Charsets.UTF_8.name())
    }

    private fun bodyTooLargeResponse(contentLength: Long): RpcHttpBodyReadResult =
        RpcHttpBodyReadResult(
            errorResponse =
                errorResponse(
                    status = NanoHTTPD.Response.Status.PAYLOAD_TOO_LARGE,
                    message = "request body too large",
                    details =
                        JSONObject()
                            .put("reason", "request_body_too_large")
                            .put("max", maxBodyBytes)
                            .put("contentLength", contentLength),
                ),
        )

    private fun unableToReadBodyResponse(): RpcHttpBodyReadResult =
        RpcHttpBodyReadResult(
            errorResponse =
                errorResponse(
                    status = NanoHTTPD.Response.Status.BAD_REQUEST,
                    message = "unable to read request body",
                ),
        )

    private fun errorResponse(
        status: NanoHTTPD.Response.Status,
        message: String,
        details: JSONObject = JSONObject(),
    ): NanoHTTPD.Response =
        jsonResponse(
            status = status,
            body =
                RpcEnvelope.error(
                    id = null,
                    code = RpcErrorCode.INVALID_REQUEST,
                    message = message,
                    retryable = false,
                    details = details,
                ),
        )

    private data class ContentLengthParseResult(
        val value: Long? = null,
        val errorResponse: NanoHTTPD.Response? = null,
    )

    private class RpcRequestBodyTooLargeException(
        val contentLength: Long,
    ) : Exception()

    private companion object {
        const val CONTENT_LENGTH_HEADER = "content-length"
        const val READ_BUFFER_BYTES = 8192
        const val END_OF_STREAM = -1
    }
}

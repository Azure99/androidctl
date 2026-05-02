package com.rainng.androidctl.agent.rpc

import android.annotation.SuppressLint
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.codec.JsonReader
import org.json.JSONArray
import org.json.JSONObject

data class RpcRequestEnvelope(
    val id: String?,
    val method: String,
    val params: JSONObject,
)

@SuppressLint("SyntheticAccessor")
object RpcRequestParser {
    fun parse(rawBody: String): RpcRequestEnvelope {
        val request = parseRequestObject(rawBody)
        val reader = JsonReader.fromObject(request)

        // Validate id first so later envelope errors only echo a strictly validated request id.
        val id = reader.optionalString("id", "id must be a string")
        val method = parseMethod(reader, id)
        val params = parseParams(reader, id)

        return RpcRequestEnvelope(
            id = id,
            method = method,
            params = params ?: JSONObject(),
        )
    }

    private fun parseMethod(
        reader: JsonReader,
        requestId: String?,
    ): String {
        val method =
            try {
                reader.requiredString(
                    key = "method",
                    missingMessage = "method is required",
                    invalidMessage = "method must be a non-blank string",
                )
            } catch (error: RequestValidationException) {
                throw error.withRequestId(requestId)
            }
        if (method.isBlank()) {
            throw RequestValidationException(
                message = "method must be a non-blank string",
                requestId = requestId,
            )
        }
        return method
    }

    private fun parseParams(
        reader: JsonReader,
        requestId: String?,
    ): JSONObject? =
        try {
            reader.optionalObject("params", "params must be a JSON object")?.toJsonObject()
        } catch (error: RequestValidationException) {
            throw error.withRequestId(requestId)
        }

    private fun parseRequestObject(rawBody: String): JSONObject {
        val trimmedBody = rawBody.trimStart()
        if (trimmedBody.isEmpty()) {
            throw RequestValidationException("request body must be valid JSON")
        }

        return when (trimmedBody.first()) {
            '{' -> parseJsonObject(rawBody)
            '[', '"', '-', in '0'..'9' -> throwIfValidNonObjectJsonValue(rawBody)
            't' -> throwIfExactLiteral(trimmedBody, "true")
            'f' -> throwIfExactLiteral(trimmedBody, "false")
            'n' -> throwIfExactLiteral(trimmedBody, "null")
            else -> throw RequestValidationException("request body must be valid JSON")
        }
    }

    private fun parseJsonObject(rawBody: String): JSONObject =
        try {
            JSONObject(rawBody)
        } catch (_: Exception) {
            throw RequestValidationException("request body must be valid JSON")
        }

    private fun throwIfValidNonObjectJsonValue(rawBody: String): Nothing {
        try {
            // Wrapping in an array lets org.json validate scalar and array payloads without
            // coercing the top-level RPC envelope into an object.
            JSONArray("[$rawBody]")
        } catch (_: Exception) {
            throw RequestValidationException("request body must be valid JSON")
        }
        throw RequestValidationException("request body must be a JSON object")
    }

    private fun throwIfExactLiteral(
        trimmedBody: String,
        literal: String,
    ): Nothing {
        if (trimmedBody.trim() != literal) {
            throw RequestValidationException("request body must be valid JSON")
        }
        throw RequestValidationException("request body must be a JSON object")
    }
}

private fun RequestValidationException.withRequestId(requestId: String?): RequestValidationException =
    if (requestId == null || this.requestId == requestId) {
        this
    } else {
        RequestValidationException(message = message, requestId = requestId)
    }

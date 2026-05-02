package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.auth.BearerTokenAuthorizer
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.runtime.RuntimeReadiness

internal class RpcAuthorizationGate(
    private val expectedTokenProvider: () -> String,
    private val readinessProvider: () -> RuntimeReadiness,
) {
    fun authorize(
        requestId: String?,
        headers: Map<String, String>,
    ): String? {
        val bearerToken = BearerTokenAuthorizer.extractBearerToken(headers)
        val authBlockedMessage = readinessProvider().authBlockedMessage
        return when {
            bearerToken == null -> unauthorized(requestId)
            authBlockedMessage != null ->
                RpcEnvelope.error(
                    id = requestId,
                    code = RpcErrorCode.RUNTIME_NOT_READY,
                    message = authBlockedMessage,
                    retryable = true,
                )
            else -> authorizeBearer(requestId, bearerToken)
        }
    }

    private fun authorizeBearer(
        requestId: String?,
        bearerToken: String,
    ): String? {
        val expectedToken = expectedTokenProvider()
        return if (expectedToken.isNotBlank() && bearerToken == expectedToken) {
            null
        } else {
            unauthorized(requestId)
        }
    }

    private fun unauthorized(requestId: String?): String =
        RpcEnvelope.error(
            id = requestId,
            code = RpcErrorCode.UNAUTHORIZED,
            message = "missing or invalid bearer token",
            retryable = false,
        )
}

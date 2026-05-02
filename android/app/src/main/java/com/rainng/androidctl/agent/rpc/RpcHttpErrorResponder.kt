package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.errors.RpcErrorCode
import fi.iki.elonen.NanoHTTPD

internal class RpcHttpErrorResponder {
    fun unexpected(
        error: Exception,
        onError: (String) -> Unit,
        logError: (String, Throwable?) -> Unit,
    ): NanoHTTPD.Response {
        onError("unexpected RPC server failure: ${error.message}")
        logError("unexpected RPC server failure", error)
        return jsonResponse(
            status = NanoHTTPD.Response.Status.OK,
            body =
                RpcEnvelope.error(
                    id = null,
                    code = RpcErrorCode.INTERNAL_ERROR,
                    message = error.message ?: "unexpected internal error",
                    retryable = true,
                ),
        )
    }
}

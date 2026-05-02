package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.AgentConstants
import com.rainng.androidctl.agent.errors.RpcErrorCode
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject

internal class RpcHttpRequestValidator {
    fun validate(
        uri: String,
        method: NanoHTTPD.Method,
    ): NanoHTTPD.Response? {
        if (uri != AgentConstants.RPC_PATH) {
            return jsonResponse(
                status = NanoHTTPD.Response.Status.NOT_FOUND,
                body =
                    RpcEnvelope.error(
                        id = null,
                        code = RpcErrorCode.INVALID_REQUEST,
                        message = "path not found",
                        retryable = false,
                        details = JSONObject().put("path", uri),
                    ),
            )
        }

        if (method != NanoHTTPD.Method.POST) {
            return jsonResponse(
                status = NanoHTTPD.Response.Status.METHOD_NOT_ALLOWED,
                body =
                    RpcEnvelope.error(
                        id = null,
                        code = RpcErrorCode.INVALID_REQUEST,
                        message = "use POST /rpc",
                        retryable = false,
                        details =
                            JSONObject()
                                .put("path", uri)
                                .put("method", method.name),
                    ),
            )
        }

        return null
    }
}

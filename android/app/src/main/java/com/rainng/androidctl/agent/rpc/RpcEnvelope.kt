package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.errors.RpcErrorCode
import org.json.JSONObject

object RpcEnvelope {
    fun success(
        id: String?,
        result: JSONObject = JSONObject(),
    ): String =
        JSONObject()
            .put("id", id)
            .put("ok", true)
            .put("result", result)
            .toString()

    fun error(
        id: String?,
        code: RpcErrorCode,
        message: String,
        retryable: Boolean,
        details: JSONObject = JSONObject(),
    ): String {
        val error =
            JSONObject()
                .put("code", code.name)
                .put("message", message)
                .put("retryable", retryable)
                .put("details", details)

        return JSONObject()
            .put("id", id)
            .put("ok", false)
            .put("error", error)
            .toString()
    }
}

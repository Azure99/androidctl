package com.rainng.androidctl.agent.rpc

import org.json.JSONObject

internal class PreparedRpcCall private constructor(
    val timeoutMs: Long,
    private val execution: PreparedExecution<*>,
) {
    fun executeEncoded(): JSONObject = execution.executeAndEncode()

    companion object {
        fun <T> typed(
            timeoutMs: Long,
            execute: () -> T,
            encode: (T) -> JSONObject,
        ): PreparedRpcCall =
            PreparedRpcCall(
                timeoutMs = timeoutMs,
                execution = PreparedExecution.Typed(execute = execute, encode = encode),
            )
    }

    private sealed interface PreparedExecution<T> {
        fun executeAndEncode(): JSONObject

        class Typed<T>(
            private val execute: () -> T,
            private val encode: (T) -> JSONObject,
        ) : PreparedExecution<T> {
            override fun executeAndEncode(): JSONObject {
                val value = execute()
                require(value !is JSONObject) {
                    "PreparedRpcCall.typed requires typed results, not pre-encoded JSONObject payloads"
                }
                return encode(value)
            }
        }
    }
}

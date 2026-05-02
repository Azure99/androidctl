package com.rainng.androidctl.agent.rpc

internal class RpcDispatchBoundary(
    private val requestHandler: RpcRequestDelegate,
) {
    fun dispatch(
        headers: Map<String, String>,
        body: String,
    ): String = requestHandler.handle(headers, body)

    fun shutdown(force: Boolean) {
        requestHandler.shutdown(force)
    }
}

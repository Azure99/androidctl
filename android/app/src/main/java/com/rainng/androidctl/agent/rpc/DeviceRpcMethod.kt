package com.rainng.androidctl.agent.rpc

internal interface DeviceRpcMethod {
    val name: String
    val policy: RpcMethodPolicy

    fun prepare(request: RpcRequestEnvelope): PreparedRpcCall
}

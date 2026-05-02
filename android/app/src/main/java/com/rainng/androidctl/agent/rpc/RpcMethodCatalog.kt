package com.rainng.androidctl.agent.rpc

internal class RpcMethodCatalog(
    methods: List<DeviceRpcMethod>,
) {
    private val methodsByName = buildMethodMap(methods)

    fun find(name: String): DeviceRpcMethod? = methodsByName[name]

    fun methodNames(): Set<String> = methodsByName.keys.toSet()

    private companion object {
        fun buildMethodMap(methods: List<DeviceRpcMethod>): Map<String, DeviceRpcMethod> {
            val methodsByName = linkedMapOf<String, DeviceRpcMethod>()
            methods.forEach { method ->
                require(methodsByName.put(method.name, method) == null) {
                    "duplicate RPC method name registered: ${method.name}"
                }
            }
            return methodsByName
        }
    }
}

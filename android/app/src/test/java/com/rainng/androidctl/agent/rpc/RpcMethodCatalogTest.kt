package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.errors.RpcErrorCode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class RpcMethodCatalogTest {
    @Test
    fun methodNamesReturnsRegisteredMethodNameSet() {
        val catalog =
            RpcMethodCatalog(
                listOf(
                    fakeMethod("meta.get"),
                    fakeMethod("snapshot.get"),
                ),
            )

        assertEquals(setOf("meta.get", "snapshot.get"), catalog.methodNames())
        assertFalse(catalog.methodNames().contains("raw.rpc"))
    }

    @Test
    fun duplicateMethodNamesAreRejected() {
        try {
            RpcMethodCatalog(
                listOf(
                    fakeMethod("meta.get"),
                    fakeMethod("meta.get"),
                ),
            )
        } catch (error: IllegalArgumentException) {
            assertEquals("duplicate RPC method name registered: meta.get", error.message)
            return
        }

        throw AssertionError("expected IllegalArgumentException")
    }

    private fun fakeMethod(name: String): DeviceRpcMethod =
        object : DeviceRpcMethod {
            override val name: String = name
            override val policy: RpcMethodPolicy =
                RpcMethodPolicy(
                    timeoutError = RpcErrorCode.INTERNAL_ERROR,
                    timeoutMessage = "$name timed out",
                )

            override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall {
                error("duplicate-name test does not execute RPC methods")
            }
        }
}

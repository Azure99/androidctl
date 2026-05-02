package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.BuildConfig
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.RuntimeAccess

internal class RpcEnvironment(
    runtimeAccess: RuntimeAccess? = null,
    expectedTokenProvider: (() -> String)? = null,
    val versionProvider: () -> String = { BuildConfig.VERSION_NAME },
) {
    private val runtimeAccessProvider: () -> RuntimeAccess = { runtimeAccess ?: AgentRuntimeBridge.runtimeAccessRole }

    val runtimeAccess: RuntimeAccess
        get() = runtimeAccessProvider()

    val expectedTokenProvider: () -> String =
        expectedTokenProvider ?: { this.runtimeAccess.currentDeviceToken() }
}

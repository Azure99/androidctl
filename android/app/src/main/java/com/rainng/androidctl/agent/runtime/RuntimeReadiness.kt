package com.rainng.androidctl.agent.runtime

import com.rainng.androidctl.agent.errors.RpcErrorCode

data class RuntimeReadiness(
    val accessibilityEnabled: Boolean,
    val accessibilityConnected: Boolean,
    val authBlockedMessage: String? = null,
    val authAvailable: Boolean = true,
    val serverPhase: ServerPhase = ServerPhase.RUNNING,
) {
    val ready: Boolean
        get() = blockingError() == null

    fun blockingError(): RpcErrorCode? =
        when {
            serverPhase != ServerPhase.RUNNING -> RpcErrorCode.RUNTIME_NOT_READY
            authBlockedMessage != null -> RpcErrorCode.RUNTIME_NOT_READY
            !authAvailable -> RpcErrorCode.RUNTIME_NOT_READY
            !accessibilityEnabled -> RpcErrorCode.ACCESSIBILITY_DISABLED
            !accessibilityConnected -> RpcErrorCode.RUNTIME_NOT_READY
            else -> null
        }

    companion object {
        internal fun fromFacts(runtimeFacts: RuntimeFacts): RuntimeReadiness {
            val inputs = runtimeInputs(runtimeFacts)
            val publishedAccessibilityConnected =
                inputs.accessibilityEnabled && inputs.accessibilityConnected
            return RuntimeReadiness(
                accessibilityEnabled = inputs.accessibilityEnabled,
                accessibilityConnected = publishedAccessibilityConnected,
                authBlockedMessage = runtimeFacts.auth.blockedMessage,
                authAvailable = inputs.authReady,
                serverPhase = runtimeFacts.serverPhase,
            )
        }
    }
}

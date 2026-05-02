package com.rainng.androidctl.agent.runtime

import com.rainng.androidctl.agent.AgentConstants

enum class ServerPhase {
    STOPPED,
    RUNNING,
    STOPPING,
}

data class AgentRuntimeState(
    val serverPhase: ServerPhase = ServerPhase.STOPPED,
    val serverRunning: Boolean = false,
    val serverHost: String = AgentConstants.DEFAULT_HOST,
    val serverPort: Int = AgentConstants.DEFAULT_PORT,
    val deviceToken: String = "",
    val authBlockedMessage: String? = null,
    val accessibilityEnabled: Boolean = false,
    val accessibilityConnected: Boolean = false,
    val runtimeReady: Boolean = false,
    val lastError: String? = null,
    val lastRequestSummary: String? = null,
)

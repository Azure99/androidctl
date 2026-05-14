package com.rainng.androidctl

import androidx.annotation.StringRes
import com.rainng.androidctl.agent.runtime.AgentRuntimeState
import com.rainng.androidctl.agent.runtime.ServerPhase

internal enum class AgentMainStatus {
    TOKEN_UNAVAILABLE,
    ACCESSIBILITY_REQUIRED,
    ACCESSIBILITY_NOT_CONNECTED,
    SERVER_STOPPED,
    SERVER_STOPPING,
    READY,
    NEEDS_ATTENTION,
}

internal data class AgentStatusUiModel(
    val mainStatus: AgentMainStatus,
    @param:StringRes val mainStatusTitleRes: Int,
    @param:StringRes val mainStatusDetailRes: Int,
    @param:StringRes val serverStatusRes: Int,
    val serverHost: String,
    val serverPort: Int,
    val bindAddress: String,
    @param:StringRes val accessibilityEnabledStatusRes: Int,
    @param:StringRes val accessibilityConnectedStatusRes: Int,
    @param:StringRes val tokenStatusRes: Int,
    val deviceToken: String,
    val authBlockedMessage: String?,
    val lastRequestSummary: String?,
    val lastError: String?,
)

internal fun AgentRuntimeState.toAgentStatusUiModel(): AgentStatusUiModel {
    val mainStatus = displayMainStatus()
    return AgentStatusUiModel(
        mainStatus = mainStatus,
        mainStatusTitleRes = mainStatus.titleRes,
        mainStatusDetailRes = mainStatus.detailRes,
        serverStatusRes = serverPhase.statusRes,
        serverHost = serverHost,
        serverPort = serverPort,
        bindAddress = "$serverHost:$serverPort",
        accessibilityEnabledStatusRes =
            if (accessibilityEnabled) {
                R.string.status_accessibility_enabled
            } else {
                R.string.status_accessibility_disabled
            },
        accessibilityConnectedStatusRes =
            if (accessibilityConnected) {
                R.string.status_accessibility_connected
            } else {
                R.string.status_accessibility_disconnected
            },
        tokenStatusRes =
            if (deviceToken.isBlank() || authBlockedMessage != null) {
                R.string.status_token_unavailable
            } else {
                R.string.status_token_available
            },
        deviceToken = deviceToken,
        authBlockedMessage = authBlockedMessage,
        lastRequestSummary = lastRequestSummary,
        lastError = lastError,
    )
}

private fun AgentRuntimeState.displayMainStatus(): AgentMainStatus =
    when {
        authBlockedMessage != null || deviceToken.isBlank() -> AgentMainStatus.TOKEN_UNAVAILABLE
        !accessibilityEnabled -> AgentMainStatus.ACCESSIBILITY_REQUIRED
        !accessibilityConnected -> AgentMainStatus.ACCESSIBILITY_NOT_CONNECTED
        serverPhase == ServerPhase.STOPPED -> AgentMainStatus.SERVER_STOPPED
        serverPhase == ServerPhase.STOPPING -> AgentMainStatus.SERVER_STOPPING
        serverPhase == ServerPhase.RUNNING && runtimeReady -> AgentMainStatus.READY
        else -> AgentMainStatus.NEEDS_ATTENTION
    }

private val ServerPhase.statusRes: Int
    @StringRes
    get() =
        when (this) {
            ServerPhase.RUNNING -> R.string.status_server_running
            ServerPhase.STOPPING -> R.string.status_server_stopping
            ServerPhase.STOPPED -> R.string.status_server_stopped
        }

private val AgentMainStatus.titleRes: Int
    @StringRes
    get() =
        when (this) {
            AgentMainStatus.TOKEN_UNAVAILABLE -> R.string.main_status_token_unavailable
            AgentMainStatus.ACCESSIBILITY_REQUIRED -> R.string.main_status_accessibility_required
            AgentMainStatus.ACCESSIBILITY_NOT_CONNECTED ->
                R.string.main_status_accessibility_not_connected
            AgentMainStatus.SERVER_STOPPED -> R.string.main_status_server_stopped
            AgentMainStatus.SERVER_STOPPING -> R.string.main_status_server_stopping
            AgentMainStatus.READY -> R.string.main_status_ready
            AgentMainStatus.NEEDS_ATTENTION -> R.string.main_status_needs_attention
        }

private val AgentMainStatus.detailRes: Int
    @StringRes
    get() =
        when (this) {
            AgentMainStatus.TOKEN_UNAVAILABLE -> R.string.main_status_token_unavailable_detail
            AgentMainStatus.ACCESSIBILITY_REQUIRED ->
                R.string.main_status_accessibility_required_detail
            AgentMainStatus.ACCESSIBILITY_NOT_CONNECTED ->
                R.string.main_status_accessibility_not_connected_detail
            AgentMainStatus.SERVER_STOPPED -> R.string.main_status_server_stopped_detail
            AgentMainStatus.SERVER_STOPPING -> R.string.main_status_server_stopping_detail
            AgentMainStatus.READY -> R.string.main_status_ready_detail
            AgentMainStatus.NEEDS_ATTENTION -> R.string.main_status_needs_attention_detail
        }

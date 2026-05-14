package com.rainng.androidctl

import com.rainng.androidctl.agent.runtime.AgentRuntimeState
import com.rainng.androidctl.agent.runtime.ServerPhase
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AgentStatusUiModelTest {
    @Test
    fun mapsReadyState() {
        val model = readyState().toAgentStatusUiModel()

        assertEquals(AgentMainStatus.READY, model.mainStatus)
        assertEquals(R.string.main_status_ready, model.mainStatusTitleRes)
        assertEquals(R.string.status_server_running, model.serverStatusRes)
        assertEquals(R.string.status_token_available, model.tokenStatusRes)
        assertEquals("127.0.0.1:8765", model.bindAddress)
    }

    @Test
    fun prioritizesAuthBlockedTokenBeforeOtherStates() {
        val model =
            readyState(
                authBlockedMessage = "token store unavailable",
                accessibilityEnabled = false,
                serverPhase = ServerPhase.STOPPED,
                runtimeReady = false,
            ).toAgentStatusUiModel()

        assertEquals(AgentMainStatus.TOKEN_UNAVAILABLE, model.mainStatus)
        assertEquals(R.string.status_token_unavailable, model.tokenStatusRes)
        assertEquals("token store unavailable", model.authBlockedMessage)
    }

    @Test
    fun prioritizesBlankTokenBeforeOtherStates() {
        val model =
            readyState(
                deviceToken = "",
                accessibilityEnabled = false,
                serverPhase = ServerPhase.STOPPED,
                runtimeReady = false,
            ).toAgentStatusUiModel()

        assertEquals(AgentMainStatus.TOKEN_UNAVAILABLE, model.mainStatus)
    }

    @Test
    fun mapsAccessibilityDisabledBeforeServerStopped() {
        val model =
            readyState(
                accessibilityEnabled = false,
                accessibilityConnected = false,
                serverPhase = ServerPhase.STOPPED,
                runtimeReady = false,
            ).toAgentStatusUiModel()

        assertEquals(AgentMainStatus.ACCESSIBILITY_REQUIRED, model.mainStatus)
        assertEquals(R.string.status_accessibility_disabled, model.accessibilityEnabledStatusRes)
    }

    @Test
    fun mapsAccessibilityEnabledButDisconnected() {
        val model =
            readyState(
                accessibilityConnected = false,
                runtimeReady = false,
            ).toAgentStatusUiModel()

        assertEquals(AgentMainStatus.ACCESSIBILITY_NOT_CONNECTED, model.mainStatus)
        assertEquals(R.string.status_accessibility_disconnected, model.accessibilityConnectedStatusRes)
    }

    @Test
    fun mapsServerStoppedAndStopping() {
        val stopped =
            readyState(
                serverPhase = ServerPhase.STOPPED,
                runtimeReady = false,
            ).toAgentStatusUiModel()
        val stopping =
            readyState(
                serverPhase = ServerPhase.STOPPING,
                runtimeReady = false,
            ).toAgentStatusUiModel()

        assertEquals(AgentMainStatus.SERVER_STOPPED, stopped.mainStatus)
        assertEquals(R.string.status_server_stopped, stopped.serverStatusRes)
        assertEquals(AgentMainStatus.SERVER_STOPPING, stopping.mainStatus)
        assertEquals(R.string.status_server_stopping, stopping.serverStatusRes)
    }

    @Test
    fun mapsNeedsAttentionWhenRunningButRuntimeReadyIsFalse() {
        val model = readyState(runtimeReady = false).toAgentStatusUiModel()

        assertEquals(AgentMainStatus.NEEDS_ATTENTION, model.mainStatus)
    }

    @Test
    fun preservesDiagnosticValuesAndNullEmptyStates() {
        val emptyDiagnostics = readyState().toAgentStatusUiModel()
        val populatedDiagnostics =
            readyState(
                lastRequestSummary = "snapshot.get",
                lastError = "rpc failure",
            ).toAgentStatusUiModel()

        assertNull(emptyDiagnostics.lastRequestSummary)
        assertNull(emptyDiagnostics.lastError)
        assertEquals("snapshot.get", populatedDiagnostics.lastRequestSummary)
        assertEquals("rpc failure", populatedDiagnostics.lastError)
    }

    private fun readyState(
        serverPhase: ServerPhase = ServerPhase.RUNNING,
        deviceToken: String = "device-token",
        authBlockedMessage: String? = null,
        accessibilityEnabled: Boolean = true,
        accessibilityConnected: Boolean = true,
        runtimeReady: Boolean = true,
        lastRequestSummary: String? = null,
        lastError: String? = null,
    ): AgentRuntimeState =
        AgentRuntimeState(
            serverPhase = serverPhase,
            serverRunning = serverPhase == ServerPhase.RUNNING,
            serverHost = "127.0.0.1",
            serverPort = 8765,
            deviceToken = deviceToken,
            authBlockedMessage = authBlockedMessage,
            accessibilityEnabled = accessibilityEnabled,
            accessibilityConnected = accessibilityConnected,
            runtimeReady = runtimeReady,
            lastRequestSummary = lastRequestSummary,
            lastError = lastError,
        )
}

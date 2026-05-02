package com.rainng.androidctl

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.AgentRuntimeState
import com.rainng.androidctl.agent.runtime.RuntimeStatusAccess
import com.rainng.androidctl.agent.runtime.ServerPhase
import com.rainng.androidctl.ui.theme.AndroidCtlTheme

class MainActivity : ComponentActivity() {
    private val runtimeStatusAccess: RuntimeStatusAccess
        get() = AgentRuntimeBridge.runtimeStatusAccessRole

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        runtimeStatusAccess.initialize(applicationContext)
        runtimeStatusAccess.refreshStatus()

        setContent {
            AgentStatusApp(
                runtimeStatusAccess = runtimeStatusAccess,
                actions =
                    AgentStatusActions(
                        onStartServer = { startServer() },
                        onStopServer = { stopServer() },
                        onOpenAccessibilitySettings = { openAccessibilitySettings() },
                        onRegenerateToken = { runtimeStatusAccess.regenerateDeviceToken() },
                        onRefreshStatus = { runtimeStatusAccess.refreshStatus() },
                    ),
            )
        }
    }

    private fun startServer() {
        startAgentServer(this)
    }

    private fun stopServer() {
        stopAgentServer(this)
    }

    private fun openAccessibilitySettings() {
        openAgentAccessibilitySettings(this)
    }

    override fun onResume() {
        super.onResume()
        runtimeStatusAccess.refreshStatus()
    }
}

@Composable
internal fun AgentStatusApp(
    runtimeStatusAccess: RuntimeStatusAccess,
    actions: AgentStatusActions,
) {
    AndroidCtlTheme {
        val state by runtimeStatusAccess.state.collectAsState()
        Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
            AgentStatusScreen(
                state = state,
                actions = actions,
                modifier = Modifier.padding(innerPadding),
            )
        }
    }
}

@Composable
internal fun AgentStatusScreen(
    state: AgentRuntimeState,
    actions: AgentStatusActions,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier =
            modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = stringResource(R.string.app_name),
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = stringResource(R.string.status_subtitle),
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        RuntimeStatusCard(state = state)
        SecurityStatusCard(state = state)
        ServerActionButtons(actions = actions)
        AccessibilityActionButtons(actions = actions)
        Button(onClick = actions.onRegenerateToken, modifier = Modifier.fillMaxWidth()) {
            Text(text = stringResource(R.string.action_regenerate_token))
        }
        NextStepsCard()

        Spacer(modifier = Modifier.height(8.dp))
    }
}

internal data class AgentStatusActions(
    val onStartServer: () -> Unit,
    val onStopServer: () -> Unit,
    val onOpenAccessibilitySettings: () -> Unit,
    val onRegenerateToken: () -> Unit,
    val onRefreshStatus: () -> Unit,
)

@Composable
private fun RuntimeStatusCard(state: AgentRuntimeState) {
    StatusCard(
        title = stringResource(R.string.section_runtime),
        lines = runtimeStatusLines(state = state),
    )
}

@Composable
private fun runtimeStatusLines(state: AgentRuntimeState): List<String> =
    listOf(
        when (state.serverPhase) {
            ServerPhase.RUNNING -> stringResource(R.string.status_server_running)
            ServerPhase.STOPPING -> stringResource(R.string.status_server_stopping)
            ServerPhase.STOPPED -> stringResource(R.string.status_server_stopped)
        },
        if (state.accessibilityEnabled) {
            stringResource(R.string.status_accessibility_enabled)
        } else {
            stringResource(R.string.status_accessibility_disabled)
        },
        if (state.accessibilityConnected) {
            stringResource(R.string.status_accessibility_connected)
        } else {
            stringResource(R.string.status_accessibility_disconnected)
        },
        if (state.runtimeReady) {
            stringResource(R.string.status_runtime_ready)
        } else {
            stringResource(R.string.status_runtime_not_ready)
        },
        stringResource(
            R.string.server_address_format,
            state.serverHost,
            state.serverPort,
        ),
    ) +
        listOfNotNull(
            state.lastRequestSummary?.let { stringResource(R.string.last_request_format, it) },
            state.lastError?.let { stringResource(R.string.last_error_format, it) },
        )

@Composable
private fun SecurityStatusCard(state: AgentRuntimeState) {
    StatusCard(
        title = stringResource(R.string.section_security),
        lines =
            listOf(
                stringResource(R.string.device_token_label),
                state.deviceToken.ifBlank { state.authBlockedMessage.orEmpty() },
            ),
    )
}

@Composable
private fun ServerActionButtons(actions: AgentStatusActions) {
    StatusActionRow(
        primaryLabel = stringResource(R.string.action_start_server),
        onPrimaryClick = actions.onStartServer,
        secondaryLabel = stringResource(R.string.action_stop_server),
        onSecondaryClick = actions.onStopServer,
    )
}

@Composable
private fun AccessibilityActionButtons(actions: AgentStatusActions) {
    StatusActionRow(
        primaryLabel = stringResource(R.string.action_open_accessibility_settings),
        onPrimaryClick = actions.onOpenAccessibilitySettings,
        secondaryLabel = stringResource(R.string.action_refresh_status),
        onSecondaryClick = actions.onRefreshStatus,
    )
}

@Composable
private fun NextStepsCard() {
    StatusCard(
        title = stringResource(R.string.section_next_steps),
        lines =
            listOf(
                stringResource(R.string.next_step_enable_accessibility),
                stringResource(R.string.next_step_start_server),
                stringResource(R.string.next_step_share_token),
            ),
    )
}

@Composable
private fun StatusActionRow(
    primaryLabel: String,
    onPrimaryClick: () -> Unit,
    secondaryLabel: String,
    onSecondaryClick: () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Button(onClick = onPrimaryClick, modifier = Modifier.weight(1f)) {
            Text(text = primaryLabel)
        }
        Button(onClick = onSecondaryClick, modifier = Modifier.weight(1f)) {
            Text(text = secondaryLabel)
        }
    }
}

@Composable
private fun StatusCard(
    title: String,
    lines: List<String>,
    modifier: Modifier = Modifier,
) {
    Card(modifier = modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Medium,
            )
            lines.forEach { line ->
                Text(text = line, style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

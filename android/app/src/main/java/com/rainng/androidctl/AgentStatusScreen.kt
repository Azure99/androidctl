package com.rainng.androidctl

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.rainng.androidctl.agent.runtime.AgentRuntimeState

@Composable
internal fun AgentStatusScreen(
    state: AgentRuntimeState,
    backgroundReliabilityState: BackgroundReliabilityState,
    actions: AgentStatusActions,
    modifier: Modifier = Modifier,
) {
    val uiModel = state.toAgentStatusUiModel()
    val backgroundReliabilityUiModel = backgroundReliabilityState.toBackgroundReliabilityUiModel()
    Column(
        modifier =
            modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        StatusHeader(uiModel = uiModel)
        ConnectionCard(uiModel = uiModel, actions = actions)
        AccessibilityCard(uiModel = uiModel, actions = actions)
        BackgroundReliabilityCard(uiModel = backgroundReliabilityUiModel, actions = actions)
        OemBackgroundChecklistCard()
        SecurityTokenCard(uiModel = uiModel, actions = actions)
        DiagnosticsCard(uiModel = uiModel)
        NextStepsCard()
        Spacer(modifier = Modifier.height(8.dp))
    }
}

@Composable
private fun StatusHeader(uiModel: AgentStatusUiModel) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
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
        Surface(
            color = MaterialTheme.colorScheme.primaryContainer,
            contentColor = MaterialTheme.colorScheme.onPrimaryContainer,
            shape = RoundedCornerShape(8.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    text = stringResource(uiModel.mainStatusTitleRes),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    text = stringResource(uiModel.mainStatusDetailRes),
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}

@Composable
private fun ConnectionCard(
    uiModel: AgentStatusUiModel,
    actions: AgentStatusActions,
) {
    StatusInfoCard(titleRes = R.string.section_connection) {
        StatusRow(R.string.field_rpc_server, stringResource(uiModel.serverStatusRes))
        StatusRow(R.string.field_host, uiModel.serverHost)
        StatusRow(R.string.field_port, uiModel.serverPort.toString())
        StatusRow(R.string.field_bind_address, uiModel.bindAddress, monospace = true)
        VerticalActions {
            Button(onClick = actions.onStartServer, modifier = Modifier.fillMaxWidth()) {
                Text(text = stringResource(R.string.action_start_server))
            }
            TextButton(onClick = actions.onStopServer, modifier = Modifier.fillMaxWidth()) {
                Text(text = stringResource(R.string.action_stop_server))
            }
        }
    }
}

@Composable
private fun AccessibilityCard(
    uiModel: AgentStatusUiModel,
    actions: AgentStatusActions,
) {
    StatusInfoCard(titleRes = R.string.section_accessibility) {
        StatusRow(
            R.string.field_accessibility_system_service,
            stringResource(uiModel.accessibilityEnabledStatusRes),
        )
        StatusRow(
            R.string.field_accessibility_runtime,
            stringResource(uiModel.accessibilityConnectedStatusRes),
        )
        VerticalActions {
            Button(
                onClick = actions.onOpenAccessibilitySettings,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(text = stringResource(R.string.action_open_accessibility_settings))
            }
            TextButton(onClick = actions.onRefreshStatus, modifier = Modifier.fillMaxWidth()) {
                Text(text = stringResource(R.string.action_refresh_status))
            }
        }
    }
}

@Composable
private fun BackgroundReliabilityCard(
    uiModel: BackgroundReliabilityUiModel,
    actions: AgentStatusActions,
) {
    StatusInfoCard(titleRes = R.string.section_background_reliability) {
        StatusRow(
            R.string.field_battery_optimization_status,
            stringResource(uiModel.batteryOptimizationStatusRes),
        )
        Text(
            text = stringResource(uiModel.batteryOptimizationDetailRes),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        VerticalActions {
            Button(
                onClick = actions.onOpenBatteryOptimizationSettings,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(text = stringResource(R.string.action_open_battery_optimization_settings))
            }
            TextButton(onClick = actions.onOpenAppInfo, modifier = Modifier.fillMaxWidth()) {
                Text(text = stringResource(R.string.action_open_app_info))
            }
        }
    }
}

@Composable
private fun SecurityTokenCard(
    uiModel: AgentStatusUiModel,
    actions: AgentStatusActions,
) {
    val clipboardManager = LocalClipboardManager.current
    val tokenText =
        uiModel.deviceToken.ifBlank {
            uiModel.authBlockedMessage ?: stringResource(R.string.device_token_empty)
        }
    StatusInfoCard(titleRes = R.string.section_security_token) {
        StatusRow(R.string.field_device_token_status, stringResource(uiModel.tokenStatusRes))
        Text(
            text = stringResource(R.string.device_token_label),
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = tokenText.softWrapDisplay(),
            style = MaterialTheme.typography.bodyMedium,
            fontFamily = FontFamily.Monospace,
            modifier = Modifier.fillMaxWidth(),
        )
        TextButton(
            onClick = { clipboardManager.setText(AnnotatedString(uiModel.deviceToken)) },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(text = stringResource(R.string.action_copy_token))
        }
        Text(
            text = stringResource(R.string.regenerate_token_note),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Button(onClick = actions.onRegenerateToken, modifier = Modifier.fillMaxWidth()) {
            Text(text = stringResource(R.string.action_regenerate_token))
        }
    }
}

@Composable
private fun DiagnosticsCard(uiModel: AgentStatusUiModel) {
    StatusInfoCard(titleRes = R.string.section_diagnostics) {
        StatusRow(
            R.string.field_last_request,
            uiModel.lastRequestSummary ?: stringResource(R.string.last_request_empty),
            monospace = uiModel.lastRequestSummary != null,
        )
        StatusRow(
            R.string.field_last_error,
            uiModel.lastError ?: stringResource(R.string.last_error_empty),
            monospace = uiModel.lastError != null,
        )
    }
}

@Composable
private fun NextStepsCard() {
    StatusInfoCard(titleRes = R.string.section_next_steps) {
        Text(text = stringResource(R.string.next_step_enable_accessibility))
        Text(text = stringResource(R.string.next_step_start_server))
        Text(text = stringResource(R.string.next_step_share_token))
    }
}

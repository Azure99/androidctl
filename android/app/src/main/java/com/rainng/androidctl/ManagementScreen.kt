package com.rainng.androidctl

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.SecureFlagPolicy
import java.util.concurrent.TimeUnit

@Composable
@Suppress("LongParameterList", "MagicNumber") // The screen names each operation and preserves the existing palette.
internal fun ManagementScreen(
    facts: ManagementState,
    readings: LocalManagementReadings,
    onGatewayEnabled: (Boolean) -> Unit,
    onAuthorize: () -> String?,
    onRefresh: () -> Unit,
    onBatterySettings: () -> String?,
    onStopExecutor: () -> Unit,
    onOpenPairingWindow: () -> Unit,
    onClosePairingWindow: () -> Unit,
    onRevokePairing: (String) -> String,
) {
    MaterialTheme(colorScheme = lightColorScheme(primary = Color(0xFF256C54), secondary = Color(0xFF526466))) {
        Surface(Modifier.fillMaxSize()) {
            Column(
                Modifier
                    .fillMaxSize()
                    .safeDrawingPadding()
                    .verticalScroll(rememberScrollState())
                    .padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Image(painterResource(R.drawable.ic_launcher_foreground), contentDescription = null, Modifier.size(40.dp))
                    Text(stringResource(R.string.app_name), style = MaterialTheme.typography.headlineSmall)
                    Text(BuildConfig.VERSION_NAME, style = MaterialTheme.typography.labelMedium)
                }
                GatewayControls(facts, readings.shizukuState, onGatewayEnabled, onAuthorize, onRefresh)
                BatteryControls(facts, readings.batteryExempt, onBatterySettings)
                HorizontalDivider()
                SessionControls(facts, onStopExecutor)
                HorizontalDivider()
                PairingControls(facts, readings, onOpenPairingWindow, onClosePairingWindow, onRevokePairing)
            }
        }
    }
}

@Composable
// Shizuku/Binder failures must be visible in the permission UI.
private fun GatewayControls(
    facts: ManagementState,
    shizukuState: String,
    onGatewayEnabled: (Boolean) -> Unit,
    onAuthorize: () -> String?,
    onRefresh: () -> Unit,
) {
    val gateway = facts.gateway.state
    val gatewayName = stringResource(R.string.gateway)
    var authorizationError by remember { mutableStateOf<String?>(null) }
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
        Column {
            Text(stringResource(R.string.gateway))
            Text(
                when (gateway) {
                    GatewayState.STARTING -> stringResource(R.string.gateway_starting)
                    GatewayState.LISTENING -> stringResource(R.string.gateway_listening)
                    GatewayState.STOPPING -> stringResource(R.string.gateway_stopping)
                    GatewayState.STOPPED -> stringResource(R.string.gateway_stopped)
                    GatewayState.FAILED -> stringResource(R.string.gateway_failed)
                },
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Switch(
            checked = gateway in listOf(GatewayState.STARTING, GatewayState.LISTENING, GatewayState.FAILED),
            onCheckedChange = onGatewayEnabled,
            modifier = Modifier.semantics { contentDescription = gatewayName },
            enabled = facts.controlsEnabled,
        )
    }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = {
            authorizationError = onAuthorize()
        }, enabled = facts.controlsEnabled) { Text(stringResource(R.string.authorize_shizuku)) }
        TextButton(onClick = onRefresh, enabled = facts.controlsEnabled) { Text(stringResource(R.string.refresh_status)) }
    }
    Text(shizukuState, style = MaterialTheme.typography.bodySmall)
    Text(stringResource(if (facts.executor != null) R.string.executor_attached else R.string.executor_unavailable))
    facts.executor?.workerId?.let {
        Text(stringResource(R.string.worker_identity, it.take(SHORT_ID_LENGTH)), style = MaterialTheme.typography.bodySmall)
    }
    authorizationError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    facts.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    ExecutorDiagnostics(facts)
}

@Composable
private fun ExecutorDiagnostics(facts: ManagementState) {
    facts.executor?.diagnostics?.let { diagnostics ->
        diagnostics.maintenance.forEach { (key, value) -> Text("$key: $value", color = MaterialTheme.colorScheme.error) }
    }
}

@Composable
private fun BatteryControls(
    facts: ManagementState,
    batteryExempt: Boolean?,
    onBatterySettings: () -> String?,
) {
    var error by remember { mutableStateOf<String?>(null) }
    Text(
        stringResource(
            when (batteryExempt) {
                true -> R.string.battery_unrestricted
                false -> R.string.battery_optimized
                null -> R.string.battery_unchecked
            },
        ),
        style = MaterialTheme.typography.bodySmall,
    )
    TextButton(onClick = {
        error = onBatterySettings()
    }, enabled = facts.controlsEnabled) { Text(stringResource(R.string.battery_settings)) }
    error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
}

@Composable
private fun SessionControls(
    facts: ManagementState,
    onStopExecutor: () -> Unit,
) {
    val session = facts.executor?.session
    val leaseId = session?.leaseId
    Text(stringResource(R.string.current_session), style = MaterialTheme.typography.titleMedium)
    if (leaseId == null) {
        Text(stringResource(if (session == null) R.string.session_unavailable else R.string.no_session))
    }
    if (session != null) SessionSummary(session)
    TextButton(
        onClick = onStopExecutor,
        enabled = facts.executor != null && facts.controlsEnabled,
    ) { Text(stringResource(R.string.stop_executor)) }
}

@Composable
private fun SessionSummary(session: SessionStatus) {
    if (session.leaseId != null) {
        Text("${displayName(session.pairingName.orEmpty())} / ${displayName(session.name.orEmpty())}")
        Text(stringResource(R.string.session_idle, session.idleSeconds))
    }
}

@Composable
private fun PairingControls(
    facts: ManagementState,
    readings: LocalManagementReadings,
    onOpenPairingWindow: () -> Unit,
    onClosePairingWindow: () -> Unit,
    onRevokePairing: (String) -> String,
) {
    Text(stringResource(R.string.pair_host), style = MaterialTheme.typography.titleMedium)
    Text(
        stringResource(R.string.pairing_expiry, TimeUnit.MILLISECONDS.toSeconds(Pairings.WINDOW_MS), Pairings.MAX_FAILURES),
        style = MaterialTheme.typography.bodySmall,
    )
    SelectionContainer {
        Text(
            stringResource(R.string.candidate_addresses, readings.candidateAddresses.joinToString()),
            style = MaterialTheme.typography.bodySmall,
        )
    }
    readings.pairingDisplay?.let { window ->
        AlertDialog(
            onDismissRequest = onClosePairingWindow,
            properties = DialogProperties(securePolicy = SecureFlagPolicy.SecureOn, dismissOnClickOutside = false),
            title = { Text(stringResource(R.string.pair_host)) },
            text = {
                Column {
                    Text(window.code, style = MaterialTheme.typography.headlineMedium)
                    Text(stringResource(R.string.pairing_remaining, window.remainingSeconds))
                }
            },
            confirmButton = { TextButton(onClick = onClosePairingWindow) { Text(stringResource(R.string.close_pairing)) } },
        )
    }
    Button(
        onClick = onOpenPairingWindow,
        enabled = facts.gateway.state == GatewayState.LISTENING,
    ) { Text(stringResource(R.string.show_pairing)) }
    var revokeMessage by remember { mutableStateOf<String?>(null) }
    readings.pairs.exceptionOrNull()?.let { Text(it.toString(), color = MaterialTheme.colorScheme.error) }
    revokeMessage?.let { Text(it) }
    facts.pendingRevocations.takeIf { it.isNotEmpty() }?.let {
        Text(stringResource(R.string.pairing_revocations_pending, it.size))
    }
    val pairs = readings.pairs.getOrNull() ?: return
    for (pair in pairs) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text(
                "${displayName(pair.name)} [${pair.id.take(SHORT_ID_LENGTH)}]",
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = {
                revokeMessage = onRevokePairing(pair.id)
            }, enabled = facts.controlsEnabled) { Text(stringResource(R.string.revoke)) }
        }
    }
}

private val ManagementState.controlsEnabled: Boolean get() = gateway.state != GatewayState.STOPPING

private const val SHORT_ID_LENGTH = 8

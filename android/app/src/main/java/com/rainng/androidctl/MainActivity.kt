package com.rainng.androidctl

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.RuntimeStatusAccess
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
                        onOpenAppInfo = { openAppInfo() },
                        onOpenBatteryOptimizationSettings = { openBatteryOptimizationSettings() },
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

    private fun openAppInfo() {
        openAgentAppInfo(this)
    }

    private fun openBatteryOptimizationSettings() {
        openAgentBatteryOptimizationSettings(this)
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
    backgroundReliabilityAccess: BackgroundReliabilityAccess = AndroidBackgroundReliabilityAccess,
) {
    AndroidCtlTheme {
        val state by runtimeStatusAccess.state.collectAsState()
        val backgroundReliabilityState = rememberBackgroundReliabilityState(backgroundReliabilityAccess)
        Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
            AgentStatusScreen(
                state = state,
                backgroundReliabilityState = backgroundReliabilityState,
                actions = actions,
                modifier = Modifier.padding(innerPadding),
            )
        }
    }
}

internal data class AgentStatusActions(
    val onStartServer: () -> Unit,
    val onStopServer: () -> Unit,
    val onOpenAccessibilitySettings: () -> Unit,
    val onOpenAppInfo: () -> Unit,
    val onOpenBatteryOptimizationSettings: () -> Unit,
    val onRegenerateToken: () -> Unit,
    val onRefreshStatus: () -> Unit,
)

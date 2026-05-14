package com.rainng.androidctl

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.rainng.androidctl.agent.auth.HostTokenProvisioning
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.RuntimeStatusAccess

class SetupActivity : ComponentActivity() {
    private var setupRuntimeInitialized = false

    private val runtimeStatusAccess: RuntimeStatusAccess
        get() = AgentRuntimeBridge.runtimeStatusAccessRole

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handleSetupIntent(intent)

        setContent {
            AgentStatusApp(
                runtimeStatusAccess = runtimeStatusAccess,
                actions =
                    AgentStatusActions(
                        onStartServer = { startAgentServer(this) },
                        onStopServer = { stopAgentServer(this) },
                        onOpenAccessibilitySettings = { openAgentAccessibilitySettings(this) },
                        onOpenAppInfo = { openAgentAppInfo(this) },
                        onOpenBatteryOptimizationSettings = {
                            openAgentBatteryOptimizationSettings(this)
                        },
                        onRegenerateToken = { runtimeStatusAccess.regenerateDeviceToken() },
                        onRefreshStatus = { runtimeStatusAccess.refreshStatus() },
                    ),
            )
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleSetupIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        if (setupRuntimeInitialized) {
            runtimeStatusAccess.refreshStatus()
        }
    }

    private fun handleSetupIntent(intent: Intent?) {
        SetupIntentHandler
            .handle(
                action = intent?.action,
                payloadReader = {
                    val extras = intent?.extras
                    SetupIntentPayload(
                        extraKeys = extras?.keySet().orEmpty(),
                        deviceToken = intent?.getStringExtra(HostTokenProvisioning.EXTRA_DEVICE_TOKEN),
                    )
                },
                startServer = { startAgentServer(this) },
                provisionDeviceToken = { token ->
                    runtimeStatusAccess.initializeWithDeviceToken(
                        context = applicationContext,
                        token = token,
                    )
                },
            ).also { validation ->
                if (validation.accepted) {
                    setupRuntimeInitialized = true
                    runtimeStatusAccess.refreshStatus()
                }
            }
    }
}

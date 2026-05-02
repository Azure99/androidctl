package com.rainng.androidctl.agent.runtime

import android.content.Context

internal class RuntimeProbeReconciler(
    var accessibilityServiceEnabledProbe: (Context) -> Boolean,
    var serverRunningProbe: (Context) -> Boolean,
    var warningLogger: (String) -> Unit,
) {
    fun reconcile(
        context: Context?,
        currentFacts: RuntimeFacts,
        accessibilityAttached: Boolean,
    ): RuntimeFacts =
        if (context == null) {
            currentFacts.copy(accessibilityAttached = accessibilityAttached)
        } else {
            currentFacts.copy(
                serverPhase =
                    reconcileServerPhase(
                        hintedPhase = currentFacts.serverPhase,
                        probeRunning =
                            probeServerRunning(
                                context = context,
                                serverRunningProbe = serverRunningProbe,
                                warningLogger = warningLogger,
                            ),
                    ),
                accessibilityEnabled =
                    probeAccessibilityEnabled(
                        context = context,
                        accessibilityServiceEnabledProbe = accessibilityServiceEnabledProbe,
                        warningLogger = warningLogger,
                    ),
                accessibilityAttached = accessibilityAttached,
            )
        }
}

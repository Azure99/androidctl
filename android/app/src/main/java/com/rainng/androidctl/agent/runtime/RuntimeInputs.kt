package com.rainng.androidctl.agent.runtime

internal data class RuntimeInputs(
    val serverPhase: ServerPhase = ServerPhase.STOPPED,
    val accessibilityEnabled: Boolean = false,
    val accessibilityConnected: Boolean = false,
    val authReady: Boolean = true,
)

internal fun runtimeInputs(runtimeFacts: RuntimeFacts): RuntimeInputs =
    RuntimeInputs(
        serverPhase = runtimeFacts.serverPhase,
        accessibilityEnabled = runtimeFacts.accessibilityEnabled,
        accessibilityConnected = runtimeFacts.accessibilityAttached,
        authReady =
            runtimeFacts.auth.available &&
                !runtimeFacts.auth.blocked &&
                !runtimeFacts.auth.currentToken.isNullOrBlank(),
    )

internal fun reconcileServerPhase(
    hintedPhase: ServerPhase,
    probeRunning: Boolean,
): ServerPhase =
    when {
        !probeRunning -> ServerPhase.STOPPED
        // STOPPING is a transient hint only while the service still appears alive.
        hintedPhase == ServerPhase.STOPPING -> ServerPhase.STOPPING
        else -> ServerPhase.RUNNING
    }

internal fun reconciledRuntimeState(
    baseState: AgentRuntimeState,
    runtimeInputs: RuntimeInputs,
): AgentRuntimeState {
    val publishedAccessibilityConnected =
        runtimeInputs.accessibilityEnabled && runtimeInputs.accessibilityConnected

    return baseState.copy(
        serverPhase = runtimeInputs.serverPhase,
        serverRunning = runtimeInputs.serverPhase == ServerPhase.RUNNING,
        accessibilityEnabled = runtimeInputs.accessibilityEnabled,
        accessibilityConnected = publishedAccessibilityConnected,
        runtimeReady = runtimeReady(runtimeInputs.copy(accessibilityConnected = publishedAccessibilityConnected)),
    )
}

internal fun reconciledRuntimeState(
    baseState: AgentRuntimeState,
    runtimeFacts: RuntimeFacts,
): AgentRuntimeState =
    reconciledRuntimeState(
        baseState =
            baseState.copy(
                deviceToken = runtimeFacts.auth.currentToken.orEmpty(),
                authBlockedMessage = runtimeFacts.auth.blockedMessage,
            ),
        runtimeInputs = runtimeInputs(runtimeFacts),
    )

internal fun runtimeReady(runtimeInputs: RuntimeInputs): Boolean =
    runtimeInputs.serverPhase == ServerPhase.RUNNING &&
        runtimeInputs.authReady &&
        runtimeInputs.accessibilityEnabled &&
        runtimeInputs.accessibilityConnected

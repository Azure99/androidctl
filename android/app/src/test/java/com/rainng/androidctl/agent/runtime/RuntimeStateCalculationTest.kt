package com.rainng.androidctl.agent.runtime

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeStateCalculationTest {
    @Test
    fun reconcileServerPhaseReturnsStoppedWhenProbeDoesNotSeeServer() {
        assertEquals(
            ServerPhase.STOPPED,
            reconcileServerPhase(
                hintedPhase = ServerPhase.RUNNING,
                probeRunning = false,
            ),
        )
    }

    @Test
    fun reconcileServerPhasePreservesStoppingWhileProbeStillSeesServer() {
        assertEquals(
            ServerPhase.STOPPING,
            reconcileServerPhase(
                hintedPhase = ServerPhase.STOPPING,
                probeRunning = true,
            ),
        )
    }

    @Test
    fun reconcileServerPhaseReturnsRunningForLiveNonStoppingServer() {
        assertEquals(
            ServerPhase.RUNNING,
            reconcileServerPhase(
                hintedPhase = ServerPhase.STOPPED,
                probeRunning = true,
            ),
        )
    }

    @Test
    fun reconciledRuntimeStateDerivesFlagsFromInputs() {
        val reconciled =
            reconciledRuntimeState(
                baseState =
                    AgentRuntimeState(
                        deviceToken = "token-1",
                        authBlockedMessage = "blocked",
                        lastError = "kept",
                    ),
                runtimeInputs =
                    RuntimeInputs(
                        serverPhase = ServerPhase.RUNNING,
                        accessibilityEnabled = true,
                        accessibilityConnected = true,
                        authReady = false,
                    ),
            )

        assertEquals(ServerPhase.RUNNING, reconciled.serverPhase)
        assertTrue(reconciled.serverRunning)
        assertTrue(reconciled.accessibilityEnabled)
        assertTrue(reconciled.accessibilityConnected)
        assertFalse(reconciled.runtimeReady)
        assertEquals("token-1", reconciled.deviceToken)
        assertEquals("blocked", reconciled.authBlockedMessage)
        assertEquals("kept", reconciled.lastError)
    }

    @Test
    fun reconciledRuntimeStateMasksConnectedAndReadinessWhenAccessibilityIsDisabled() {
        val reconciled =
            reconciledRuntimeState(
                baseState = AgentRuntimeState(),
                runtimeInputs =
                    RuntimeInputs(
                        serverPhase = ServerPhase.RUNNING,
                        accessibilityEnabled = false,
                        accessibilityConnected = true,
                        authReady = true,
                    ),
            )

        assertTrue(reconciled.serverRunning)
        assertFalse(reconciled.accessibilityEnabled)
        assertFalse(reconciled.accessibilityConnected)
        assertFalse(reconciled.runtimeReady)
    }

    @Test
    fun reconciledRuntimeStateCopiesStableRuntimeFieldsWithoutChangingStateShape() {
        val reconciled =
            reconciledRuntimeState(
                baseState =
                    AgentRuntimeState(
                        serverHost = "127.0.0.1",
                        serverPort = 4242,
                        lastError = "request failed",
                        lastRequestSummary = "POST /rpc",
                    ),
                runtimeFacts =
                    RuntimeFacts(
                        auth = AuthFacts(currentToken = "token-1", blocked = false, blockedMessage = null, available = true),
                        serverPhase = ServerPhase.RUNNING,
                        accessibilityEnabled = true,
                        accessibilityAttached = true,
                    ),
            )

        assertEquals("token-1", reconciled.deviceToken)
        assertEquals(ServerPhase.RUNNING, reconciled.serverPhase)
        assertTrue(reconciled.serverRunning)
        assertTrue(reconciled.accessibilityEnabled)
        assertTrue(reconciled.accessibilityConnected)
        assertTrue(reconciled.runtimeReady)
        assertEquals("127.0.0.1", reconciled.serverHost)
        assertEquals(4242, reconciled.serverPort)
        assertEquals("request failed", reconciled.lastError)
        assertEquals("POST /rpc", reconciled.lastRequestSummary)
    }

    @Test
    fun reconciledRuntimeStateFromFactsMasksConnectedWhenAccessibilityIsDisabled() {
        val reconciled =
            reconciledRuntimeState(
                baseState = AgentRuntimeState(),
                runtimeFacts =
                    RuntimeFacts(
                        auth = AuthFacts(currentToken = "token-1", available = true),
                        serverPhase = ServerPhase.RUNNING,
                        accessibilityEnabled = false,
                        accessibilityAttached = true,
                    ),
            )

        assertFalse(reconciled.accessibilityEnabled)
        assertFalse(reconciled.accessibilityConnected)
        assertFalse(reconciled.runtimeReady)
    }

    @Test
    fun runtimeReadyRequiresRunningEnabledConnectedAndAuthReady() {
        assertFalse(
            runtimeReady(
                RuntimeInputs(
                    serverPhase = ServerPhase.STOPPED,
                    accessibilityEnabled = true,
                    accessibilityConnected = true,
                    authReady = true,
                ),
            ),
        )
        assertFalse(
            runtimeReady(
                RuntimeInputs(
                    serverPhase = ServerPhase.RUNNING,
                    accessibilityEnabled = false,
                    accessibilityConnected = true,
                    authReady = true,
                ),
            ),
        )
        assertFalse(
            runtimeReady(
                RuntimeInputs(
                    serverPhase = ServerPhase.RUNNING,
                    accessibilityEnabled = true,
                    accessibilityConnected = true,
                    authReady = false,
                ),
            ),
        )
        assertTrue(
            runtimeReady(
                RuntimeInputs(
                    serverPhase = ServerPhase.RUNNING,
                    accessibilityEnabled = true,
                    accessibilityConnected = true,
                    authReady = true,
                ),
            ),
        )
    }
}

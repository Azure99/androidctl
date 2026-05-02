package com.rainng.androidctl.agent.runtime

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeStatusStoreTest {
    @Test
    fun updateInputsPublishesReconciledRuntimeState() {
        val publishedStates = mutableListOf<AgentRuntimeState>()
        val store = RuntimeStatusStore(runtimeStateRecorder = publishedStates::add)

        store.updateInputs(
            transform = {
                it.copy(
                    serverPhase = ServerPhase.RUNNING,
                    accessibilityEnabled = true,
                    accessibilityConnected = true,
                )
            },
        )

        with(store.currentState()) {
            assertEquals(ServerPhase.RUNNING, serverPhase)
            assertTrue(serverRunning)
            assertTrue(accessibilityEnabled)
            assertTrue(accessibilityConnected)
            assertTrue(runtimeReady)
        }
        assertEquals(listOf(store.currentState()), publishedStates)
    }

    @Test
    fun updateStatePreservesRuntimeInputs() {
        val store = RuntimeStatusStore()
        store.updateInputs(
            transform = {
                it.copy(
                    serverPhase = ServerPhase.RUNNING,
                    accessibilityEnabled = true,
                    accessibilityConnected = false,
                )
            },
        )

        store.updateState { it.copy(lastError = "failed") }

        assertEquals("failed", store.currentState().lastError)
        assertEquals(ServerPhase.RUNNING, store.currentInputs().serverPhase)
        assertTrue(store.currentInputs().accessibilityEnabled)
        assertFalse(store.currentInputs().accessibilityConnected)
    }

    @Test
    fun resetClearsInputsAndState() {
        val store = RuntimeStatusStore()
        store.updateInputs(
            transform = {
                it.copy(
                    serverPhase = ServerPhase.RUNNING,
                    accessibilityEnabled = true,
                    accessibilityConnected = true,
                )
            },
        )
        store.updateState { it.copy(deviceToken = "token-1") }

        store.reset()

        assertEquals(AgentRuntimeState(), store.currentState())
        assertEquals(RuntimeInputs(), store.currentInputs())
    }
}

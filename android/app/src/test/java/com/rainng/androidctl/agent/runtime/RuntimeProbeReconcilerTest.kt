package com.rainng.androidctl.agent.runtime

import android.content.Context
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.mock

class RuntimeProbeReconcilerTest {
    @Test
    fun reconcileUsesProbeResultsWithoutTouchingAuthFacts() {
        val context = mock(Context::class.java)
        val reconciler =
            RuntimeProbeReconciler(
                accessibilityServiceEnabledProbe = { true },
                serverRunningProbe = { false },
                warningLogger = {},
            )

        val reconciled =
            reconciler.reconcile(
                context = context,
                currentFacts =
                    RuntimeFacts(
                        serverPhase = ServerPhase.RUNNING,
                        auth =
                            AuthFacts(
                                currentToken = "token-1",
                                blocked = false,
                                available = true,
                            ),
                    ),
                accessibilityAttached = true,
            )

        assertEquals(ServerPhase.STOPPED, reconciled.serverPhase)
        assertTrue(reconciled.accessibilityEnabled)
        assertTrue(reconciled.accessibilityAttached)
        assertEquals("token-1", reconciled.auth.currentToken)
        assertFalse(reconciled.auth.blocked)
        assertTrue(reconciled.auth.available)
    }

    @Test
    fun reconcileWithoutContextOnlyUpdatesAttachmentFact() {
        val reconciler =
            RuntimeProbeReconciler(
                accessibilityServiceEnabledProbe = { true },
                serverRunningProbe = { true },
                warningLogger = {},
            )

        val reconciled =
            reconciler.reconcile(
                context = null,
                currentFacts =
                    RuntimeFacts(
                        serverPhase = ServerPhase.STOPPING,
                        auth =
                            AuthFacts(
                                currentToken = "token-1",
                                blocked = false,
                                available = true,
                            ),
                        accessibilityEnabled = true,
                        accessibilityAttached = false,
                    ),
                accessibilityAttached = true,
            )

        assertEquals(ServerPhase.STOPPING, reconciled.serverPhase)
        assertTrue(reconciled.accessibilityEnabled)
        assertTrue(reconciled.accessibilityAttached)
        assertEquals("token-1", reconciled.auth.currentToken)
        assertFalse(reconciled.auth.blocked)
        assertTrue(reconciled.auth.available)
    }
}

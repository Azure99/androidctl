package com.rainng.androidctl.agent.runtime

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeFactsStoreTest {
    @Test
    fun storesAuthAndForegroundFactsAsCanonicalTruth() {
        val store = RuntimeFactsStore()

        store.update {
            it.copy(
                serverPhase = ServerPhase.RUNNING,
                auth =
                    AuthFacts(
                        currentToken = "token-1",
                        blocked = false,
                        blockedMessage = null,
                        available = true,
                    ),
                accessibilityEnabled = true,
                accessibilityAttached = true,
                foreground =
                    ForegroundFacts(
                        hintPackageName = "com.android.settings",
                        hintActivityName = "WifiSettingsActivity",
                        generation = 4L,
                    ),
            )
        }

        val facts = store.current()
        assertEquals(ServerPhase.RUNNING, facts.serverPhase)
        assertEquals("token-1", facts.auth.currentToken)
        assertFalse(facts.auth.blocked)
        assertTrue(facts.auth.available)
        assertEquals("com.android.settings", facts.foreground.hintPackageName)
        assertEquals("WifiSettingsActivity", facts.foreground.hintActivityName)
        assertEquals(4L, facts.foreground.generation)
    }
}

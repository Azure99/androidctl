package com.rainng.androidctl.agent.runtime

import com.rainng.androidctl.agent.auth.DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE
import com.rainng.androidctl.agent.errors.RpcErrorCode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeReadinessTest {
    @Test
    fun projectsBlockedAuthFromFacts() {
        val readiness =
            RuntimeReadiness.fromFacts(
                RuntimeFacts(
                    serverPhase = ServerPhase.RUNNING,
                    auth =
                        AuthFacts(
                            currentToken = null,
                            blocked = true,
                            blockedMessage = DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE,
                            available = false,
                        ),
                    accessibilityEnabled = true,
                    accessibilityAttached = true,
                ),
            )

        assertFalse(readiness.ready)
        assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, readiness.authBlockedMessage)
        assertEquals(RpcErrorCode.RUNTIME_NOT_READY, readiness.blockingError())
    }

    @Test
    fun projectsAccessibilityDisabledFromFacts() {
        val readiness =
            RuntimeReadiness.fromFacts(
                RuntimeFacts(
                    serverPhase = ServerPhase.RUNNING,
                    auth = AuthFacts(currentToken = "token-1", blocked = false, available = true),
                    accessibilityEnabled = false,
                    accessibilityAttached = true,
                ),
            )

        assertFalse(readiness.ready)
        assertEquals(RpcErrorCode.ACCESSIBILITY_DISABLED, readiness.blockingError())
    }

    @Test
    fun projectsRuntimeNotReadyWhenServiceIsDetached() {
        val readiness =
            RuntimeReadiness.fromFacts(
                RuntimeFacts(
                    serverPhase = ServerPhase.RUNNING,
                    auth = AuthFacts(currentToken = "token-1", blocked = false, available = true),
                    accessibilityEnabled = true,
                    accessibilityAttached = false,
                ),
            )

        assertFalse(readiness.ready)
        assertEquals(RpcErrorCode.RUNTIME_NOT_READY, readiness.blockingError())
    }

    @Test
    fun projectsReadyRuntimeFromFacts() {
        val readiness =
            RuntimeReadiness.fromFacts(
                RuntimeFacts(
                    serverPhase = ServerPhase.RUNNING,
                    auth = AuthFacts(currentToken = "token-1", blocked = false, available = true),
                    accessibilityEnabled = true,
                    accessibilityAttached = true,
                ),
            )

        assertTrue(readiness.ready)
        assertNull(readiness.authBlockedMessage)
        assertNull(readiness.blockingError())
    }
}

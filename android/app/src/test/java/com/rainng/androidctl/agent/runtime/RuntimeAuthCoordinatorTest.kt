package com.rainng.androidctl.agent.runtime

import android.content.Context
import com.rainng.androidctl.agent.auth.DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeAuthCoordinatorTest {
    @Test
    fun loadInitialTokenPublishesAvailableTokenIntoFactsAndState() {
        val factsStore = RuntimeFactsStore()
        val statusStore = RuntimeStatusStore(runtimeStateRecorder = {})
        val coordinator =
            newCoordinator(
                factsStore = factsStore,
                statusStore = statusStore,
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
            )

        coordinator.loadInitialToken()

        with(factsStore.current().auth) {
            assertEquals("token-1", currentToken)
            assertFalse(blocked)
            assertNull(blockedMessage)
            assertTrue(available)
        }
        with(statusStore.currentState()) {
            assertEquals("token-1", deviceToken)
            assertNull(authBlockedMessage)
            assertNull(lastError)
        }
    }

    @Test
    fun loadInitialTokenPublishesBlockedAuthIntoFactsAndState() {
        val factsStore = RuntimeFactsStore()
        val statusStore = RuntimeStatusStore(runtimeStateRecorder = {})
        val coordinator =
            newCoordinator(
                factsStore = factsStore,
                statusStore = statusStore,
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult =
                            DeviceTokenLoadResult.Blocked(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE)

                        override fun regenerateToken(): String = "token-2"
                    },
            )

        coordinator.loadInitialToken()

        with(factsStore.current().auth) {
            assertNull(currentToken)
            assertTrue(blocked)
            assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, blockedMessage)
            assertFalse(available)
        }
        with(statusStore.currentState()) {
            assertEquals("", deviceToken)
            assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, authBlockedMessage)
            assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, lastError)
        }
    }

    @Test
    fun loadInitialTokenPreservesUnrelatedLastErrorWhenClearingBlockedAuth() {
        val factsStore =
            RuntimeFactsStore(
                initialFacts =
                    RuntimeFacts(
                        auth =
                            AuthFacts(
                                blocked = true,
                                blockedMessage = "blocked",
                            ),
                    ),
            )
        val statusStore =
            RuntimeStatusStore(
                initialState =
                    AgentRuntimeState(
                        authBlockedMessage = "blocked",
                        lastError = "request failed",
                    ),
                runtimeStateRecorder = {},
            )
        val coordinator =
            newCoordinator(
                factsStore = factsStore,
                statusStore = statusStore,
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
            )

        coordinator.loadInitialToken()

        with(statusStore.currentState()) {
            assertEquals("token-1", deviceToken)
            assertNull(authBlockedMessage)
            assertEquals("request failed", lastError)
        }
    }

    @Test
    fun regenerateTokenClearsMatchingBlockedAuthErrorAndPublishesNewToken() {
        val factsStore =
            RuntimeFactsStore(
                initialFacts =
                    RuntimeFacts(
                        auth =
                            AuthFacts(
                                blocked = true,
                                blockedMessage = "blocked",
                            ),
                    ),
            )
        val statusStore =
            RuntimeStatusStore(
                initialState =
                    AgentRuntimeState(
                        authBlockedMessage = "blocked",
                        lastError = "blocked",
                    ),
                runtimeStateRecorder = {},
            )
        val coordinator =
            newCoordinator(
                factsStore = factsStore,
                statusStore = statusStore,
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("unused")

                        override fun regenerateToken(): String = "token-2"
                    },
            )

        coordinator.regenerateToken()

        with(factsStore.current().auth) {
            assertEquals("token-2", currentToken)
            assertFalse(blocked)
            assertNull(blockedMessage)
            assertTrue(available)
        }
        with(statusStore.currentState()) {
            assertEquals("token-2", deviceToken)
            assertNull(authBlockedMessage)
            assertNull(lastError)
        }
    }

    @Test
    fun replaceTokenClearsMatchingBlockedAuthErrorAndPublishesProvidedToken() {
        val factsStore =
            RuntimeFactsStore(
                initialFacts =
                    RuntimeFacts(
                        auth =
                            AuthFacts(
                                blocked = true,
                                blockedMessage = "blocked",
                            ),
                    ),
            )
        val statusStore =
            RuntimeStatusStore(
                initialState =
                    AgentRuntimeState(
                        authBlockedMessage = "blocked",
                        lastError = "blocked",
                    ),
                runtimeStateRecorder = {},
            )
        var replacedToken: String? = null
        val coordinator =
            newCoordinator(
                factsStore = factsStore,
                statusStore = statusStore,
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("unused")

                        override fun regenerateToken(): String = "generated-token"

                        override fun replaceToken(token: String): String {
                            replacedToken = token
                            return token
                        }
                    },
            )

        coordinator.replaceToken("host-token")

        assertEquals("host-token", replacedToken)
        with(factsStore.current().auth) {
            assertEquals("host-token", currentToken)
            assertFalse(blocked)
            assertNull(blockedMessage)
            assertTrue(available)
        }
        with(statusStore.currentState()) {
            assertEquals("host-token", deviceToken)
            assertNull(authBlockedMessage)
            assertNull(lastError)
        }
    }

    private fun newCoordinator(
        factsStore: RuntimeFactsStore,
        statusStore: RuntimeStatusStore,
        deviceTokenStoreAccess: DeviceTokenStoreAccess,
    ): RuntimeAuthCoordinator =
        RuntimeAuthCoordinator(
            factsStore = factsStore,
            statusStore = statusStore,
            deviceTokenCoordinator = DeviceTokenCoordinator(deviceTokenStoreAccess),
        )
}

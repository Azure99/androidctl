package com.rainng.androidctl.agent.runtime

import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult

internal class RuntimeAuthCoordinator(
    private val factsStore: RuntimeFactsStore,
    private val statusStore: RuntimeStatusStore,
    private val deviceTokenCoordinator: DeviceTokenCoordinator,
) {
    @Synchronized
    fun loadInitialToken() {
        when (val loadResult = deviceTokenCoordinator.loadCurrentToken()) {
            is DeviceTokenLoadResult.Available ->
                publishAvailableToken(loadResult.token)

            is DeviceTokenLoadResult.Blocked ->
                publishBlockedAuth(loadResult.message)
        }
    }

    @Synchronized
    fun regenerateToken() {
        publishAvailableToken(deviceTokenCoordinator.regenerateToken())
    }

    @Synchronized
    fun replaceToken(token: String) {
        publishAvailableToken(deviceTokenCoordinator.replaceToken(token))
    }

    private fun publishAvailableToken(token: String) {
        val currentState = statusStore.currentState()
        publishAuthFacts(
            authFacts =
                AuthFacts(
                    currentToken = token,
                    blocked = false,
                    blockedMessage = null,
                    available = true,
                ),
            baseState =
                currentState.copy(
                    lastError = clearBlockedAuthError(currentState),
                ),
        )
    }

    private fun publishBlockedAuth(message: String) {
        publishAuthFacts(
            authFacts =
                AuthFacts(
                    currentToken = null,
                    blocked = true,
                    blockedMessage = message,
                    available = false,
                ),
            baseState =
                statusStore.currentState().copy(
                    lastError = message,
                ),
        )
    }

    private fun publishAuthFacts(
        authFacts: AuthFacts,
        baseState: AgentRuntimeState,
    ) {
        val nextFacts = factsStore.update { it.copy(auth = authFacts) }
        statusStore.updateInputs(
            transform = { runtimeInputs(nextFacts) },
            baseState =
                baseState.copy(
                    deviceToken = authFacts.currentToken.orEmpty(),
                    authBlockedMessage = authFacts.blockedMessage,
                ),
        )
    }

    private fun clearBlockedAuthError(state: AgentRuntimeState): String? =
        if (state.lastError == state.authBlockedMessage) {
            null
        } else {
            state.lastError
        }
}

package com.rainng.androidctl.agent.runtime

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

internal class RuntimeStatusStore(
    initialState: AgentRuntimeState = AgentRuntimeState(),
    initialInputs: RuntimeInputs = RuntimeInputs(),
    var runtimeStateRecorder: (AgentRuntimeState) -> Unit = {},
    var runtimeEventPublisher: () -> Unit = {},
) {
    private val mutableState = MutableStateFlow(initialState)
    val state: StateFlow<AgentRuntimeState> = mutableState.asStateFlow()

    private var runtimeInputs = initialInputs

    @Synchronized
    fun currentState(): AgentRuntimeState = mutableState.value

    @Synchronized
    fun currentInputs(): RuntimeInputs = runtimeInputs

    @Synchronized
    fun updateInputs(
        transform: (RuntimeInputs) -> RuntimeInputs,
        baseState: AgentRuntimeState = mutableState.value,
    ) {
        runtimeInputs = transform(runtimeInputs)
        publish(
            state = reconciledRuntimeState(baseState = baseState, runtimeInputs = runtimeInputs),
            publishRuntimeEvent = true,
        )
    }

    @Synchronized
    fun updateState(transform: (AgentRuntimeState) -> AgentRuntimeState) {
        publish(transform(mutableState.value))
    }

    @Synchronized
    fun reset() {
        runtimeInputs = RuntimeInputs()
        mutableState.value = AgentRuntimeState()
    }

    private fun publish(
        state: AgentRuntimeState,
        publishRuntimeEvent: Boolean = false,
    ) {
        mutableState.value = state
        runtimeStateRecorder(state)
        if (publishRuntimeEvent) {
            runtimeEventPublisher()
        }
    }
}

package com.rainng.androidctl.agent.runtime

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

internal class RuntimeFactsStore(
    initialFacts: RuntimeFacts = RuntimeFacts(),
) {
    private val mutableState = MutableStateFlow(initialFacts)
    val state: StateFlow<RuntimeFacts> = mutableState.asStateFlow()

    @Synchronized
    fun current(): RuntimeFacts = mutableState.value

    @Synchronized
    fun update(transform: (RuntimeFacts) -> RuntimeFacts): RuntimeFacts {
        val nextFacts = transform(mutableState.value)
        mutableState.value = nextFacts
        return nextFacts
    }

    @Synchronized
    fun reset() {
        mutableState.value = RuntimeFacts()
    }
}

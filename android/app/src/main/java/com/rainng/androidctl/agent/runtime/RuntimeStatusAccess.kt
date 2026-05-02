package com.rainng.androidctl.agent.runtime

import android.content.Context
import kotlinx.coroutines.flow.StateFlow

internal interface RuntimeStatusAccess {
    val state: StateFlow<AgentRuntimeState>

    fun initialize(context: Context)

    fun initializeWithDeviceToken(
        context: Context,
        token: String,
    )

    fun refreshStatus()

    fun regenerateDeviceToken()

    fun replaceDeviceToken(token: String)
}

internal class GraphRuntimeStatusAccess(
    override val state: StateFlow<AgentRuntimeState>,
    private val runtimeLifecycle: RuntimeLifecycle,
) : RuntimeStatusAccess {
    override fun initialize(context: Context) {
        runtimeLifecycle.initialize(context)
    }

    override fun initializeWithDeviceToken(
        context: Context,
        token: String,
    ) {
        runtimeLifecycle.initializeWithDeviceToken(context = context, token = token)
    }

    override fun refreshStatus() {
        runtimeLifecycle.reconcileRuntimeState()
    }

    override fun regenerateDeviceToken() {
        runtimeLifecycle.regenerateDeviceToken()
    }

    override fun replaceDeviceToken(token: String) {
        runtimeLifecycle.replaceDeviceToken(token)
    }
}

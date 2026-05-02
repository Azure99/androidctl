package com.rainng.androidctl.agent.service

import android.content.Context
import com.rainng.androidctl.agent.logging.AgentLog
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.RuntimeLifecycle

internal class AndroidRuntimeCallbacks(
    private val applicationContext: Context,
    runtimeLifecycle: RuntimeLifecycle? = null,
) : AgentServerController.RuntimeCallbacks {
    private val runtimeLifecycleProvider: () -> RuntimeLifecycle =
        { runtimeLifecycle ?: AgentRuntimeBridge.runtimeLifecycleAccess }

    override fun initialize() {
        runtimeLifecycleProvider().initialize(applicationContext)
    }

    override fun markServerRunning() {
        runtimeLifecycleProvider().markServerRunning()
    }

    override fun markServerStopping() {
        runtimeLifecycleProvider().markServerStopping()
    }

    override fun markServerStopped() {
        runtimeLifecycleProvider().markServerStopped()
    }

    override fun recordRequestSummary(summary: String) {
        runtimeLifecycleProvider().recordRequestSummary(summary)
    }

    override fun recordError(message: String) {
        runtimeLifecycleProvider().recordError(message)
    }
}

object AndroidAgentServerLogger : AgentServerController.Logger {
    override fun info(message: String) {
        AgentLog.i(message)
    }

    override fun error(
        message: String,
        throwable: Throwable?,
    ) {
        AgentLog.e(message, throwable)
    }
}

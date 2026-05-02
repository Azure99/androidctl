package com.rainng.androidctl.agent.service

import android.app.Service
import android.content.Intent
import android.os.IBinder
import com.rainng.androidctl.agent.logging.AgentLog

class AgentServerService : Service() {
    private lateinit var controller: AgentServerController
    private lateinit var foregroundController: AgentServerForegroundController

    override fun onCreate() {
        super.onCreate()
        AgentLog.i("AgentServerService created")
        foregroundController = AgentServerForegroundController(applicationContext)
        controller =
            AgentServerController(
                actions =
                    AgentServerActions(
                        start = ACTION_START,
                        stop = ACTION_STOP,
                    ),
                runtimeCallbacks = AndroidRuntimeCallbacks(applicationContext),
                serverFactory = NanoHttpdRpcServerFactory(),
                onStopRequested = {
                    foregroundController.stop(this)
                    stopSelf()
                },
                logger = AndroidAgentServerLogger,
            )
        controller.onCreate()
    }

    override fun onStartCommand(
        intent: Intent?,
        flags: Int,
        startId: Int,
    ): Int {
        val action = intent?.action
        if (shouldPromoteToForeground(action)) {
            AgentLog.i("AgentServerService promoting to foreground for action=${action ?: "null"}")
            foregroundController.start(this)
        }

        val result = controller.handleAction(action)
        AgentLog.i("AgentServerService handled action=${action ?: "null"} result=$result")
        if (result == AgentServerController.ActionResult.StartFailed) {
            foregroundController.stop(this)
            stopSelfResult(startId)
        }
        if (result == AgentServerController.ActionResult.Ignored && !controller.isServerRunning()) {
            foregroundController.stop(this)
            stopSelfResult(startId)
        }

        return startMode(action)
    }

    override fun onDestroy() {
        AgentLog.i("AgentServerService destroyed")
        foregroundController.stop(this)
        controller.onDestroy()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        const val ACTION_START = "com.rainng.androidctl.action.START_SERVER"
        const val ACTION_STOP = "com.rainng.androidctl.action.STOP_SERVER"

        internal fun shouldPromoteToForeground(action: String?): Boolean = action == null || action == ACTION_START

        internal fun startMode(action: String?): Int =
            if (shouldPromoteToForeground(action)) {
                Service.START_STICKY
            } else {
                Service.START_NOT_STICKY
            }
    }
}

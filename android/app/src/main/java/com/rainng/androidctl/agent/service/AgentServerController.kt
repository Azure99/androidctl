package com.rainng.androidctl.agent.service

import com.rainng.androidctl.agent.AgentConstants
import com.rainng.androidctl.agent.bootstrap.AndroidDeviceRpcFactory
import com.rainng.androidctl.agent.logging.AgentLog
import com.rainng.androidctl.agent.rpc.RpcDispatchBoundary
import com.rainng.androidctl.agent.rpc.RpcHttpBodyReader
import com.rainng.androidctl.agent.rpc.RpcHttpErrorResponder
import com.rainng.androidctl.agent.rpc.RpcHttpRequestValidator
import com.rainng.androidctl.agent.rpc.RpcHttpServer
import com.rainng.androidctl.agent.rpc.RpcHttpServerCallbacks
import com.rainng.androidctl.agent.rpc.RpcHttpServerPipeline
import com.rainng.androidctl.agent.rpc.RpcRequestAdmissionGate
import com.rainng.androidctl.agent.rpc.RpcRequestHandler
import fi.iki.elonen.NanoHTTPD

data class AgentServerActions(
    val start: String,
    val stop: String,
)

data class AgentServerEndpoint(
    val host: String = AgentConstants.DEFAULT_HOST,
    val port: Int = AgentConstants.DEFAULT_PORT,
)

class AgentServerController(
    private val actions: AgentServerActions,
    private val runtimeCallbacks: RuntimeCallbacks,
    private val serverFactory: RpcServerFactory,
    private val onStopRequested: () -> Unit = {},
    private val logger: Logger = NoOpLogger,
    private val endpoint: AgentServerEndpoint = AgentServerEndpoint(),
) {
    private data class ShutdownOutcome(
        val drained: Boolean,
    )

    private var server: RunningServer? = null
    private var stopRequested = false

    fun onCreate() {
        runtimeCallbacks.initialize()
    }

    fun handleAction(action: String?): ActionResult {
        when (action ?: actions.start) {
            actions.start -> return startServer()
            actions.stop -> return stopServer(requestServiceStop = true)
        }
        return ActionResult.Ignored
    }

    fun onDestroy(): ActionResult =
        stopServer(
            requestServiceStop = false,
            markStoppedWhenIdle = false,
        )

    private fun startServer(): ActionResult {
        if (server != null) {
            logger.info("RPC server already running")
            runtimeCallbacks.markServerRunning()
            return ActionResult.AlreadyRunning
        }

        val candidate =
            serverFactory.create(
                onRequest = runtimeCallbacks::recordRequestSummary,
                onError = runtimeCallbacks::recordError,
            )

        val startFailure =
            runCatching {
                candidate.start()
            }.exceptionOrNull()
        if (startFailure == null) {
            server = candidate
            stopRequested = false
            runtimeCallbacks.markServerRunning()
            logger.info("RPC server started on ${endpoint.host}:${endpoint.port}")
            return ActionResult.Started
        }

        runtimeCallbacks.recordError("failed to start RPC server: ${startFailure.message}")
        logger.error("failed to start RPC server", startFailure)
        runCatching {
            candidate.finishShutdown(force = true)
        }.onFailure { shutdownFailure ->
            runtimeCallbacks.recordError("failed to clean up RPC server after start failure: ${shutdownFailure.message}")
            logger.error("failed to clean up RPC server after start failure", shutdownFailure)
        }
        return ActionResult.StartFailed
    }

    fun isServerRunning(): Boolean = server != null

    private fun stopServer(
        requestServiceStop: Boolean,
        markStoppedWhenIdle: Boolean = true,
    ): ActionResult {
        val runningServer = server
        val shouldMarkStopped = runningServer != null || markStoppedWhenIdle
        val shouldRequestStop = requestServiceStop && !stopRequested

        if (!shouldMarkStopped && !shouldRequestStop) {
            return ActionResult.Ignored
        }

        val shutdownOutcome =
            runningServer?.let { activeServer ->
                runtimeCallbacks.markServerStopping()
                activeServer.beginShutdown()
                ShutdownOutcome(
                    drained = activeServer.awaitQuiescence(SERVER_DRAIN_TIMEOUT_MS),
                ).also { outcome ->
                    activeServer.finishShutdown(force = !outcome.drained)
                }
            }
        server = null
        if (shouldMarkStopped) {
            runtimeCallbacks.markServerStopped()
        }
        if (shouldRequestStop) {
            stopRequested = true
            onStopRequested()
        }
        logger.info("RPC server stopped drained=${shutdownOutcome?.drained ?: true}")
        return ActionResult.Stopped
    }

    enum class ActionResult {
        Started,
        AlreadyRunning,
        StartFailed,
        Stopped,
        Ignored,
    }

    interface RuntimeCallbacks {
        fun initialize()

        fun markServerRunning()

        fun markServerStopping()

        fun markServerStopped()

        fun recordRequestSummary(summary: String)

        fun recordError(message: String)
    }

    interface RpcServerFactory {
        fun create(
            onRequest: (String) -> Unit,
            onError: (String) -> Unit,
        ): RunningServer
    }

    interface RunningServer {
        fun start()

        fun beginShutdown()

        fun awaitQuiescence(timeoutMs: Long): Boolean

        fun finishShutdown(force: Boolean)
    }

    interface Logger {
        fun info(message: String)

        fun error(
            message: String,
            throwable: Throwable? = null,
        )
    }

    private object NoOpLogger : Logger {
        override fun info(message: String) = Unit

        override fun error(
            message: String,
            throwable: Throwable?,
        ) = Unit
    }

    private companion object {
        private const val SERVER_DRAIN_TIMEOUT_MS = 1000L
    }
}

internal class NanoHttpdRpcServerFactory(
    private val hostname: String = AgentConstants.DEFAULT_HOST,
    private val port: Int = AgentConstants.DEFAULT_PORT,
    private val requestHandlerFactory: () -> RpcRequestHandler = AndroidDeviceRpcFactory::createRequestHandler,
) : AgentServerController.RpcServerFactory {
    override fun create(
        onRequest: (String) -> Unit,
        onError: (String) -> Unit,
    ): AgentServerController.RunningServer {
        val requestHandler = requestHandlerFactory()
        val server =
            RpcHttpServer(
                hostname = hostname,
                port = port,
                callbacks =
                    RpcHttpServerCallbacks(
                        onRequest = onRequest,
                        onError = onError,
                        logError = AgentLog::e,
                    ),
                pipeline =
                    RpcHttpServerPipeline(
                        admissionGate = RpcRequestAdmissionGate(),
                        validator = RpcHttpRequestValidator(),
                        bodyReader = RpcHttpBodyReader(onError = onError, logError = AgentLog::e),
                        dispatchBoundary = RpcDispatchBoundary(requestHandler),
                        errorResponder = RpcHttpErrorResponder(),
                    ),
            )
        return object : AgentServerController.RunningServer {
            override fun start() {
                server.start(NanoHTTPD.SOCKET_READ_TIMEOUT, false)
            }

            override fun beginShutdown() {
                server.beginShutdown()
            }

            override fun awaitQuiescence(timeoutMs: Long): Boolean = server.awaitQuiescence(timeoutMs)

            override fun finishShutdown(force: Boolean) {
                server.finishShutdown(force)
            }
        }
    }
}

package com.rainng.androidctl.agent.service

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AgentServerControllerTest {
    @Test
    fun onCreateInitializesRuntimeCallbacks() {
        val runtime = FakeRuntimeCallbacks()

        newController(runtimeCallbacks = runtime).onCreate()

        assertEquals(1, runtime.initializeCalls)
    }

    @Test
    fun startSuccessStartsServerAndUpdatesRuntime() {
        val runtime = FakeRuntimeCallbacks()
        val factory = FakeRpcServerFactory()

        val result = newController(runtimeCallbacks = runtime, serverFactory = factory).handleAction("start")

        assertEquals(AgentServerController.ActionResult.Started, result)
        assertEquals(1, factory.createCalls)
        assertEquals(1, factory.createdServers.single().startCalls)
        assertEquals(1, runtime.markServerRunningCalls)
        assertNull(runtime.lastError)
    }

    @Test
    fun repeatedStartIsIdempotent() {
        val runtime = FakeRuntimeCallbacks()
        val factory = FakeRpcServerFactory()
        val controller = newController(runtimeCallbacks = runtime, serverFactory = factory)

        controller.handleAction("start")
        val result = controller.handleAction("start")

        assertEquals(AgentServerController.ActionResult.AlreadyRunning, result)
        assertEquals(1, factory.createCalls)
        assertEquals(1, factory.createdServers.single().startCalls)
        assertEquals(2, runtime.markServerRunningCalls)
    }

    @Test
    fun stopSuccessStopsRunningServerAndRequestsServiceStop() {
        val runtime = FakeRuntimeCallbacks()
        val factory = FakeRpcServerFactory()
        var stopRequests = 0
        val controller =
            newController(
                runtimeCallbacks = runtime,
                serverFactory = factory,
                onStopRequested = { stopRequests += 1 },
            )

        controller.handleAction("start")
        val result = controller.handleAction("stop")

        assertEquals(AgentServerController.ActionResult.Stopped, result)
        assertEquals(1, factory.createdServers.single().beginShutdownCalls)
        assertEquals(listOf(false), factory.createdServers.single().finishShutdownForces)
        assertEquals(1, runtime.markServerStoppingCalls)
        assertEquals(1, runtime.markServerStoppedCalls)
        assertEquals(1, stopRequests)
    }

    @Test
    fun destroyAfterExplicitStopDoesNotRepeatStopSideEffects() {
        val runtime = FakeRuntimeCallbacks()
        val factory = FakeRpcServerFactory()
        var stopRequests = 0
        val controller =
            newController(
                runtimeCallbacks = runtime,
                serverFactory = factory,
                onStopRequested = { stopRequests += 1 },
            )

        controller.handleAction("start")
        controller.handleAction("stop")
        val result = controller.onDestroy()

        assertEquals(AgentServerController.ActionResult.Ignored, result)
        assertEquals(1, factory.createdServers.single().beginShutdownCalls)
        assertEquals(listOf(false), factory.createdServers.single().finishShutdownForces)
        assertEquals(1, runtime.markServerStoppingCalls)
        assertEquals(1, runtime.markServerStoppedCalls)
        assertEquals(1, stopRequests)
    }

    @Test
    fun stopBeforeStartStillUpdatesRuntimeAndRequestsServiceStop() {
        val runtime = FakeRuntimeCallbacks()
        var stopRequests = 0

        newController(
            runtimeCallbacks = runtime,
            onStopRequested = { stopRequests += 1 },
        ).handleAction("stop")

        assertEquals(0, runtime.markServerStoppingCalls)
        assertEquals(1, runtime.markServerStoppedCalls)
        assertEquals(1, stopRequests)
    }

    @Test
    fun startFailureRecordsRuntimeErrorAndDoesNotRetainServer() {
        val runtime = FakeRuntimeCallbacks()
        val factory =
            FakeRpcServerFactory().apply {
                queuedServers += FakeRunningServer(startFailure = IllegalStateException("port busy"))
                queuedServers += FakeRunningServer()
            }
        val controller = newController(runtimeCallbacks = runtime, serverFactory = factory)

        val firstResult = controller.handleAction("start")
        val secondResult = controller.handleAction("start")

        assertEquals(AgentServerController.ActionResult.StartFailed, firstResult)
        assertEquals(AgentServerController.ActionResult.Started, secondResult)
        assertEquals(2, factory.createCalls)
        assertEquals("failed to start RPC server: port busy", runtime.lastError)
        assertEquals(1, runtime.markServerRunningCalls)
        assertEquals(1, factory.createdServers[0].startCalls)
        assertEquals(listOf(true), factory.createdServers[0].finishShutdownForces)
        assertEquals(1, factory.createdServers[1].startCalls)
        assertEquals(emptyList<Boolean>(), factory.createdServers[1].finishShutdownForces)
    }

    @Test
    fun destroyCleanupStopsRunningServer() {
        val runtime = FakeRuntimeCallbacks()
        val factory = FakeRpcServerFactory()
        var stopRequests = 0
        val controller =
            newController(
                runtimeCallbacks = runtime,
                serverFactory = factory,
                onStopRequested = { stopRequests += 1 },
            )

        controller.handleAction("start")
        val result = controller.onDestroy()

        assertEquals(AgentServerController.ActionResult.Stopped, result)
        assertEquals(1, factory.createdServers.single().beginShutdownCalls)
        assertEquals(listOf(false), factory.createdServers.single().finishShutdownForces)
        assertEquals(1, runtime.markServerStoppingCalls)
        assertEquals(1, runtime.markServerStoppedCalls)
        assertEquals(0, stopRequests)
    }

    @Test
    fun stopForcesShutdownWhenDrainTimesOut() {
        val runtime = FakeRuntimeCallbacks()
        val factory =
            FakeRpcServerFactory().apply {
                queuedServers += FakeRunningServer(awaitQuiescenceResult = false)
            }
        val controller = newController(runtimeCallbacks = runtime, serverFactory = factory)

        controller.handleAction("start")
        val result = controller.handleAction("stop")

        assertEquals(AgentServerController.ActionResult.Stopped, result)
        assertEquals(1, runtime.markServerStoppingCalls)
        assertEquals(listOf(true), factory.createdServers.single().finishShutdownForces)
        assertEquals(1, runtime.markServerStoppedCalls)
    }

    @Test
    fun serverCallbacksAreForwardedToRuntime() {
        val runtime = FakeRuntimeCallbacks()
        val factory = FakeRpcServerFactory()
        val controller = newController(runtimeCallbacks = runtime, serverFactory = factory)

        controller.handleAction("start")
        factory.lastOnRequest?.invoke("POST /rpc")
        factory.lastOnError?.invoke("request failed")

        assertEquals(listOf("POST /rpc"), runtime.requestSummaries)
        assertEquals("request failed", runtime.lastError)
    }

    @Test
    fun unknownActionIsIgnored() {
        val runtime = FakeRuntimeCallbacks()
        val factory = FakeRpcServerFactory()

        val result = newController(runtimeCallbacks = runtime, serverFactory = factory).handleAction("noop")

        assertEquals(AgentServerController.ActionResult.Ignored, result)
        assertEquals(0, factory.createCalls)
        assertEquals(0, runtime.markServerRunningCalls)
        assertEquals(0, runtime.markServerStoppedCalls)
    }

    private fun newController(
        runtimeCallbacks: FakeRuntimeCallbacks = FakeRuntimeCallbacks(),
        serverFactory: FakeRpcServerFactory = FakeRpcServerFactory(),
        onStopRequested: () -> Unit = {},
    ): AgentServerController =
        AgentServerController(
            actions =
                AgentServerActions(
                    start = "start",
                    stop = "stop",
                ),
            runtimeCallbacks = runtimeCallbacks,
            serverFactory = serverFactory,
            onStopRequested = onStopRequested,
        )

    private class FakeRuntimeCallbacks : AgentServerController.RuntimeCallbacks {
        var initializeCalls = 0
        var markServerRunningCalls = 0
        var markServerStoppingCalls = 0
        var markServerStoppedCalls = 0
        val requestSummaries = mutableListOf<String>()
        var lastError: String? = null

        override fun initialize() {
            initializeCalls += 1
        }

        override fun markServerRunning() {
            markServerRunningCalls += 1
        }

        override fun markServerStopping() {
            markServerStoppingCalls += 1
        }

        override fun markServerStopped() {
            markServerStoppedCalls += 1
        }

        override fun recordRequestSummary(summary: String) {
            requestSummaries += summary
        }

        override fun recordError(message: String) {
            lastError = message
        }
    }

    private class FakeRpcServerFactory : AgentServerController.RpcServerFactory {
        var createCalls = 0
        val queuedServers = ArrayDeque<FakeRunningServer>()
        val createdServers = mutableListOf<FakeRunningServer>()
        var lastOnRequest: ((String) -> Unit)? = null
        var lastOnError: ((String) -> Unit)? = null

        override fun create(
            onRequest: (String) -> Unit,
            onError: (String) -> Unit,
        ): AgentServerController.RunningServer {
            createCalls += 1
            lastOnRequest = onRequest
            lastOnError = onError
            val server = queuedServers.removeFirstOrNull() ?: FakeRunningServer()
            createdServers += server
            return server
        }
    }

    private class FakeRunningServer(
        private val startFailure: Throwable? = null,
        private val awaitQuiescenceResult: Boolean = true,
    ) : AgentServerController.RunningServer {
        var startCalls = 0
        var beginShutdownCalls = 0
        val finishShutdownForces = mutableListOf<Boolean>()

        override fun start() {
            startCalls += 1
            startFailure?.let { throw it }
        }

        override fun beginShutdown() {
            beginShutdownCalls += 1
        }

        override fun awaitQuiescence(timeoutMs: Long): Boolean = awaitQuiescenceResult

        override fun finishShutdown(force: Boolean) {
            finishShutdownForces += force
        }
    }
}

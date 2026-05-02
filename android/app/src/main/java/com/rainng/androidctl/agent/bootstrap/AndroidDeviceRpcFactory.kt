package com.rainng.androidctl.agent.bootstrap

import com.rainng.androidctl.agent.rpc.RpcAuthorizationGate
import com.rainng.androidctl.agent.rpc.RpcDispatcher
import com.rainng.androidctl.agent.rpc.RpcEnvironment
import com.rainng.androidctl.agent.rpc.RpcExecutionRunner
import com.rainng.androidctl.agent.rpc.RpcRequestHandler
import com.rainng.androidctl.agent.rpc.RpcShutdownResource
import com.rainng.androidctl.agent.screenshot.ScreenshotTaskRunner
import java.util.concurrent.ExecutorService

internal object AndroidDeviceRpcFactory {
    fun createRequestHandler(
        environment: RpcEnvironment = RpcEnvironment(),
        methodExecutor: ExecutorService = RpcRequestHandler.newMethodExecutor(),
        screenshotTaskRunner: ScreenshotTaskRunner = ScreenshotTaskRunner.createDefault(),
    ): RpcRequestHandler {
        val runtimeAccessProvider = { environment.runtimeAccess }
        val readinessProvider = { runtimeAccessProvider().readiness() }
        val authorizationGate =
            RpcAuthorizationGate(
                expectedTokenProvider = environment.expectedTokenProvider,
                readinessProvider = readinessProvider,
            )
        val accessibilityFactory = AccessibilityBoundExecutionFactory(environment)
        val dispatcher =
            RpcDispatcher(
                runtimeAccessProvider = runtimeAccessProvider,
                methodCatalog =
                    RpcMethodCatalogFactory(
                        environment = environment,
                        accessibilityBoundExecutionFactory = accessibilityFactory,
                        screenshotTaskRunner = screenshotTaskRunner,
                    ).create(),
                executionRunner = RpcExecutionRunner(methodExecutor),
            )
        return RpcRequestHandler(
            authorizationGate = authorizationGate,
            dispatcher = dispatcher,
            methodExecutor = methodExecutor,
            shutdownResources = listOf(RpcShutdownResource { force -> screenshotTaskRunner.shutdown(force) }),
        )
    }
}

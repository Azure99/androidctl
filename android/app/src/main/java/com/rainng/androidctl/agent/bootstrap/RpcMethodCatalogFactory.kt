package com.rainng.androidctl.agent.bootstrap

import com.rainng.androidctl.agent.actions.AccessibilityActionBackend
import com.rainng.androidctl.agent.actions.ActionPerformer
import com.rainng.androidctl.agent.device.AppsListProvider
import com.rainng.androidctl.agent.events.DeviceEventHub
import com.rainng.androidctl.agent.rpc.ActionPerformMethod
import com.rainng.androidctl.agent.rpc.AppsListMethod
import com.rainng.androidctl.agent.rpc.EventsPollMethod
import com.rainng.androidctl.agent.rpc.MetaGetMethod
import com.rainng.androidctl.agent.rpc.RpcEnvironment
import com.rainng.androidctl.agent.rpc.RpcMethodCatalog
import com.rainng.androidctl.agent.rpc.ScreenshotCaptureMethod
import com.rainng.androidctl.agent.rpc.SnapshotGetMethod
import com.rainng.androidctl.agent.runtime.AccessibilityForegroundObservationProvider
import com.rainng.androidctl.agent.screenshot.ScreenshotCapture
import com.rainng.androidctl.agent.screenshot.ScreenshotTaskRunner
import com.rainng.androidctl.agent.snapshot.SnapshotCollector

internal class RpcMethodCatalogFactory(
    private val environment: RpcEnvironment,
    private val accessibilityBoundExecutionFactory: AccessibilityBoundExecutionFactory,
    private val screenshotTaskRunner: ScreenshotTaskRunner,
) {
    fun create(): RpcMethodCatalog =
        RpcMethodCatalog(
            listOf(
                MetaGetMethod(environment.versionProvider),
                AppsListMethod {
                    AppsListProvider(checkNotNull(environment.runtimeAccess.applicationContext())).list()
                },
                EventsPollMethod(DeviceEventHub::poll),
                SnapshotGetMethod(snapshotExecutionFactory = { request ->
                    accessibilityBoundExecutionFactory.bind { service ->
                        SnapshotCollector(service).collect(
                            includeInvisible = request.includeInvisible,
                            includeSystemWindows = request.includeSystemWindows,
                        )
                    }
                }),
                ActionPerformMethod { request ->
                    accessibilityBoundExecutionFactory.bind { service ->
                        ActionPerformer(
                            backend = AccessibilityActionBackend(service),
                            observationProvider = AccessibilityForegroundObservationProvider(service),
                        ).perform(request)
                    }
                },
                ScreenshotCaptureMethod { request ->
                    accessibilityBoundExecutionFactory.bind { service ->
                        ScreenshotCapture(
                            service = service,
                            processingRunner = screenshotTaskRunner,
                        ).capture(request)
                    }
                },
            ),
        )
}

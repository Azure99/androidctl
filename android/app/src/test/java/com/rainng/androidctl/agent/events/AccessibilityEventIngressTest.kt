package com.rainng.androidctl.agent.events

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import com.rainng.androidctl.agent.runtime.AccessibilityAttachmentHandleSnapshot
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.ForegroundObservation
import com.rainng.androidctl.agent.runtime.ForegroundObservationWriter
import com.rainng.androidctl.agent.runtime.RuntimeContextStore
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.times
import org.mockito.Mockito.verify
import org.mockito.Mockito.`when`

class AccessibilityEventIngressTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
    }

    @Test
    fun recordIgnoresEventsFromStaleServiceAttachment() {
        val staleService = mock(AccessibilityService::class.java)
        val liveService = mock(AccessibilityService::class.java)
        val event = mock(AccessibilityEvent::class.java)
        `when`(event.eventType).thenReturn(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED)
        `when`(event.packageName).thenReturn("com.example.stale")
        `when`(event.className).thenReturn("StaleActivity")

        val foregroundWrites = mutableListOf<Triple<Int, String?, String?>>()
        var environmentCaptureCalls = 0
        val forwardedEvents = mutableListOf<ObservedAccessibilityEvent>()
        val forwardedEnvironments = mutableListOf<DeviceEventEnvironment>()
        val environment =
            object : DeviceEventEnvironment {
                override fun foregroundObservation(): ForegroundObservation =
                    ForegroundObservation(
                        generation = 0L,
                    )

                override fun currentImeState(): ImeState = ImeState(visible = false, windowId = null)
            }

        val ingress =
            BridgeAccessibilityEventIngress(
                observationPolicy = { true },
                attachmentHandleProvider = {
                    AccessibilityAttachmentHandleSnapshot(
                        service = liveService,
                        generation = 2L,
                        revoked = false,
                        activationUptimeMillis = 200L,
                    )
                },
                foregroundObservationWriter =
                    object : ForegroundObservationWriter {
                        override fun recordObservedWindowState(
                            eventType: Int,
                            packageName: String?,
                            windowClassName: String?,
                        ) {
                            foregroundWrites += Triple(eventType, packageName, windowClassName)
                        }

                        override fun reset() = Unit
                    },
                environmentCapture = {
                    environmentCaptureCalls += 1
                    environment
                },
                eventSink = { observedEvent, observedEnvironment ->
                    forwardedEvents += observedEvent
                    forwardedEnvironments += observedEnvironment
                },
            )

        ingress.record(staleService, event)

        assertTrue(foregroundWrites.isEmpty())
        assertEquals(0, environmentCaptureCalls)
        assertTrue(forwardedEvents.isEmpty())
        assertTrue(forwardedEnvironments.isEmpty())
    }

    @Test
    fun recordForwardsEventsForCurrentLiveAttachment() {
        val liveService = mock(AccessibilityService::class.java)
        val event = mock(AccessibilityEvent::class.java)
        `when`(event.eventType).thenReturn(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED)
        `when`(event.packageName).thenReturn("com.example.live")
        `when`(event.eventTime).thenReturn(400L)
        `when`(event.className).thenReturn("LiveActivity")

        val foregroundWrites = mutableListOf<Triple<Int, String?, String?>>()
        var environmentCaptureCalls = 0
        val forwardedEvents = mutableListOf<ObservedAccessibilityEvent>()
        val forwardedEnvironments = mutableListOf<DeviceEventEnvironment>()
        val environment =
            object : DeviceEventEnvironment {
                override fun foregroundObservation(): ForegroundObservation =
                    ForegroundObservation(
                        generation = 3L,
                    )

                override fun currentImeState(): ImeState = ImeState(visible = false, windowId = null)
            }

        val ingress =
            BridgeAccessibilityEventIngress(
                observationPolicy = { true },
                attachmentHandleProvider = {
                    AccessibilityAttachmentHandleSnapshot(
                        service = liveService,
                        generation = 4L,
                        revoked = false,
                        activationUptimeMillis = 300L,
                    )
                },
                foregroundObservationWriter =
                    object : ForegroundObservationWriter {
                        override fun recordObservedWindowState(
                            eventType: Int,
                            packageName: String?,
                            windowClassName: String?,
                        ) {
                            foregroundWrites += Triple(eventType, packageName, windowClassName)
                        }

                        override fun reset() = Unit
                    },
                environmentCapture = {
                    environmentCaptureCalls += 1
                    environment
                },
                eventSink = { observedEvent, observedEnvironment ->
                    forwardedEvents += observedEvent
                    forwardedEnvironments += observedEnvironment
                },
            )

        ingress.record(liveService, event)

        assertEquals(
            listOf(
                Triple(
                    AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                    "com.example.live",
                    "LiveActivity",
                ),
            ),
            foregroundWrites,
        )
        assertEquals(1, environmentCaptureCalls)
        assertEquals(
            listOf(
                ObservedAccessibilityEvent(
                    eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            ),
            forwardedEvents,
        )
        assertEquals(listOf(environment), forwardedEnvironments)
    }

    @Test
    fun recordIgnoresQueuedEventFromSameServiceInstanceAfterReattach() {
        val reusedService = mock(AccessibilityService::class.java)
        val event = mock(AccessibilityEvent::class.java)
        `when`(event.eventType).thenReturn(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED)
        `when`(event.packageName).thenReturn("com.example.queued")
        `when`(event.className).thenReturn("QueuedActivity")
        `when`(event.eventTime).thenReturn(150L)

        val foregroundWrites = mutableListOf<Triple<Int, String?, String?>>()
        var environmentCaptureCalls = 0
        val forwardedEvents = mutableListOf<ObservedAccessibilityEvent>()
        val forwardedEnvironments = mutableListOf<DeviceEventEnvironment>()
        val environment =
            object : DeviceEventEnvironment {
                override fun foregroundObservation(): ForegroundObservation =
                    ForegroundObservation(
                        generation = 5L,
                    )

                override fun currentImeState(): ImeState = ImeState(visible = false, windowId = null)
            }

        val ingress =
            BridgeAccessibilityEventIngress(
                observationPolicy = { true },
                attachmentHandleProvider = {
                    AccessibilityAttachmentHandleSnapshot(
                        service = reusedService,
                        generation = 2L,
                        revoked = false,
                        activationUptimeMillis = 200L,
                    )
                },
                foregroundObservationWriter =
                    object : ForegroundObservationWriter {
                        override fun recordObservedWindowState(
                            eventType: Int,
                            packageName: String?,
                            windowClassName: String?,
                        ) {
                            foregroundWrites += Triple(eventType, packageName, windowClassName)
                        }

                        override fun reset() = Unit
                    },
                environmentCapture = {
                    environmentCaptureCalls += 1
                    environment
                },
                eventSink = { observedEvent, observedEnvironment ->
                    forwardedEvents += observedEvent
                    forwardedEnvironments += observedEnvironment
                },
            )

        ingress.record(reusedService, event)

        assertTrue(foregroundWrites.isEmpty())
        assertEquals(0, environmentCaptureCalls)
        assertTrue(forwardedEvents.isEmpty())
        assertTrue(forwardedEnvironments.isEmpty())
        verify(event, times(1)).eventTime
    }

    @Test
    fun recordForwardsEventFromDifferentServiceInstanceAfterSameMillisecondReattach() {
        val store = RuntimeContextStore { 100L }
        val staleService = mock(AccessibilityService::class.java)
        val liveService = mock(AccessibilityService::class.java)
        store.registerAccessibilityService(staleService)
        store.unregisterAccessibilityService()
        store.registerAccessibilityService(liveService)

        val event = mock(AccessibilityEvent::class.java)
        `when`(event.eventType).thenReturn(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED)
        `when`(event.packageName).thenReturn("com.example.fastreattach")
        `when`(event.className).thenReturn("FastReattachActivity")
        `when`(event.eventTime).thenReturn(100L)

        val foregroundWrites = mutableListOf<Triple<Int, String?, String?>>()
        var environmentCaptureCalls = 0
        val forwardedEvents = mutableListOf<ObservedAccessibilityEvent>()
        val forwardedEnvironments = mutableListOf<DeviceEventEnvironment>()
        val environment =
            object : DeviceEventEnvironment {
                override fun foregroundObservation(): ForegroundObservation =
                    ForegroundObservation(
                        generation = 6L,
                    )

                override fun currentImeState(): ImeState = ImeState(visible = false, windowId = null)
            }

        val ingress =
            BridgeAccessibilityEventIngress(
                observationPolicy = { true },
                attachmentHandleProvider = store::currentAccessibilityAttachmentHandle,
                foregroundObservationWriter =
                    object : ForegroundObservationWriter {
                        override fun recordObservedWindowState(
                            eventType: Int,
                            packageName: String?,
                            windowClassName: String?,
                        ) {
                            foregroundWrites += Triple(eventType, packageName, windowClassName)
                        }

                        override fun reset() = Unit
                    },
                environmentCapture = {
                    environmentCaptureCalls += 1
                    environment
                },
                eventSink = { observedEvent, observedEnvironment ->
                    forwardedEvents += observedEvent
                    forwardedEnvironments += observedEnvironment
                },
            )

        ingress.record(liveService, event)

        assertEquals(
            listOf(
                Triple(
                    AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                    "com.example.fastreattach",
                    "FastReattachActivity",
                ),
            ),
            foregroundWrites,
        )
        assertEquals(1, environmentCaptureCalls)
        assertEquals(
            listOf(
                ObservedAccessibilityEvent(
                    eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            ),
            forwardedEvents,
        )
        assertEquals(listOf(environment), forwardedEnvironments)
    }

    @Test
    fun defaultForegroundObservationWriterFollowsLatestBridgeGraphAfterReset() {
        val liveService = mock(AccessibilityService::class.java)
        val event = mock(AccessibilityEvent::class.java)
        `when`(event.eventType).thenReturn(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED)
        `when`(event.packageName).thenReturn("com.example.latest")
        `when`(event.className).thenReturn("LatestActivity")
        `when`(event.eventTime).thenReturn(250L)
        val environment =
            object : DeviceEventEnvironment {
                override fun foregroundObservation(): ForegroundObservation = ForegroundObservation(generation = 1L)

                override fun currentImeState(): ImeState = ImeState(visible = false, windowId = null)
            }
        val ingress =
            BridgeAccessibilityEventIngress(
                observationPolicy = { true },
                attachmentHandleProvider = {
                    AccessibilityAttachmentHandleSnapshot(
                        service = liveService,
                        generation = 1L,
                        revoked = false,
                        activationUptimeMillis = 100L,
                    )
                },
                environmentCapture = { environment },
                eventSink = { _, _ -> },
            )

        AgentRuntimeBridge.resetForTest()

        ingress.record(liveService, event)

        assertEquals("com.example.latest", AgentRuntimeBridge.currentForegroundHintState.fallbackPackageName)
        assertEquals(1L, AgentRuntimeBridge.currentForegroundGeneration)
    }
}

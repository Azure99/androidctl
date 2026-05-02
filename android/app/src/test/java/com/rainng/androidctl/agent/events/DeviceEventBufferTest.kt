package com.rainng.androidctl.agent.events

import com.rainng.androidctl.agent.rpc.codec.JsonWriter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.lang.reflect.InvocationTargetException

class DeviceEventBufferTest {
    @Test
    fun publishDerivesTypeFromPayload() {
        val buffer = DeviceEventBuffer(capacity = 8, timestampProvider = { "2026-03-15T00:00:00Z" })
        val event =
            buffer.publish(
                PackageChangedPayload(
                    packageName = "com.android.settings",
                    activityName = null,
                ),
            )

        assertEquals("package.changed", event.type)
    }

    @Test
    fun codecPreservesWireTypeTokensForAllCurrentPayloads() {
        val events =
            listOf(
                DeviceEvent(seq = 1L, timestamp = "2026-03-27T00:00:00Z", data = RuntimeStatusPayload(true, true, true, true)),
                DeviceEvent(
                    seq = 2L,
                    timestamp = "2026-03-27T00:00:01Z",
                    data = PackageChangedPayload(packageName = "com.android.settings", activityName = null),
                ),
                DeviceEvent(seq = 3L, timestamp = "2026-03-27T00:00:02Z", data = WindowChangedPayload(null, null, "windowsChanged")),
                DeviceEvent(seq = 4L, timestamp = "2026-03-27T00:00:03Z", data = FocusChangedPayload(null, null, "viewFocused")),
                DeviceEvent(seq = 5L, timestamp = "2026-03-27T00:00:04Z", data = ImeChangedPayload(visible = false, windowId = null)),
                DeviceEvent(
                    seq = 6L,
                    timestamp = "2026-03-27T00:00:05Z",
                    data = SnapshotInvalidatedPayload(packageName = null, reason = "viewScrolled"),
                ),
            )

        val writer = JsonWriter.objectWriter()
        EventPollResultCodec.write(writer, EventPollResult(events = events, latestSeq = 6L, needResync = false, timedOut = false))
        val encoded = writer.toJsonObject().getJSONArray("events")

        assertEquals("runtime.status", encoded.getJSONObject(0).getString("type"))
        assertEquals("package.changed", encoded.getJSONObject(1).getString("type"))
        assertEquals("window.changed", encoded.getJSONObject(2).getString("type"))
        assertEquals("focus.changed", encoded.getJSONObject(3).getString("type"))
        assertEquals("ime.changed", encoded.getJSONObject(4).getString("type"))
        assertEquals("snapshot.invalidated", encoded.getJSONObject(5).getString("type"))
    }

    @Test
    fun pollReturnsEventsInSequenceOrder() {
        val buffer = DeviceEventBuffer(capacity = 8, timestampProvider = { "2026-03-15T00:00:00Z" })
        buffer.publish(
            PackageChangedPayload(
                packageName = "com.android.settings",
                activityName = null,
            ),
        )
        buffer.publish(
            SnapshotInvalidatedPayload(
                packageName = "com.android.settings",
                reason = "windowStateChanged",
            ),
        )

        val result =
            buffer.poll(
                EventPollRequest(
                    afterSeq = 0L,
                    waitMs = 0L,
                    limit = 20,
                ),
            )

        assertEquals(listOf(1L, 2L), result.events.map(DeviceEvent::seq))
        assertEquals(listOf("package.changed", "snapshot.invalidated"), result.events.map(DeviceEvent::type))
        assertEquals(2L, result.latestSeq)
        assertFalse(result.needResync)
        assertFalse(result.timedOut)
    }

    @Test
    fun pollReturnsTypedPayloadSnapshotsAcrossCalls() {
        val buffer = DeviceEventBuffer(capacity = 8, timestampProvider = { "2026-03-15T00:00:00Z" })
        val payload =
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            )
        buffer.publish(payload)

        val first = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))
        val second = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))

        assertEquals(payload, first.events.single().data)
        assertEquals(payload, second.events.single().data)
        assertNotSame(first.events.single(), second.events.single())
    }

    @Test
    fun deviceEventTypeIsDerivedFromPayloadSubtype() {
        val event =
            DeviceEvent(
                seq = 1L,
                timestamp = "2026-03-27T00:00:00Z",
                data = SnapshotInvalidatedPayload(packageName = null, reason = "viewClicked"),
            )

        assertEquals("snapshot.invalidated", event.type)
    }

    @Test
    fun reasonBearingPayloadsRejectNullReason() {
        assertNullReasonRejected(WindowChangedPayload::class.java, null, null, null)
        assertNullReasonRejected(FocusChangedPayload::class.java, null, null, null)
        assertNullReasonRejected(SnapshotInvalidatedPayload::class.java, null, null)
    }

    @Test
    fun pollTimesOutWhenNoEventsArrive() {
        val buffer = DeviceEventBuffer()

        val result =
            buffer.poll(
                EventPollRequest(
                    afterSeq = 0L,
                    waitMs = 25L,
                    limit = 20,
                ),
            )

        assertTrue(result.events.isEmpty())
        assertTrue(result.timedOut)
        assertEquals(0L, result.latestSeq)
    }

    @Test
    fun pollMarksNeedResyncWhenCursorFallsBehindBufferWindow() {
        val buffer = DeviceEventBuffer(capacity = 2, timestampProvider = { "2026-03-15T00:00:00Z" })
        buffer.publish(
            RuntimeStatusPayload(
                serverRunning = false,
                accessibilityEnabled = false,
                accessibilityConnected = false,
                runtimeReady = false,
            ),
        )
        buffer.publish(
            PackageChangedPayload(
                packageName = "com.android.settings",
                activityName = null,
            ),
        )
        buffer.publish(
            SnapshotInvalidatedPayload(
                packageName = "com.android.settings",
                reason = "windowStateChanged",
            ),
        )
        buffer.publish(
            WindowChangedPayload(
                packageName = "com.android.settings",
                activityName = null,
                reason = "windowsChanged",
            ),
        )

        val result =
            buffer.poll(
                EventPollRequest(
                    afterSeq = 1L,
                    waitMs = 0L,
                    limit = 20,
                ),
            )

        assertTrue(result.needResync)
        assertEquals(listOf(3L, 4L), result.events.map(DeviceEvent::seq))
        assertEquals(4L, result.latestSeq)
    }

    @Test
    fun resetClearsBufferedEventsWithoutForcingNewConsumersToResync() {
        val buffer = DeviceEventBuffer(capacity = 8, timestampProvider = { "2026-03-15T00:00:00Z" })
        buffer.publish(
            PackageChangedPayload(
                packageName = "com.android.settings",
                activityName = null,
            ),
        )

        buffer.reset()

        val result =
            buffer.poll(
                EventPollRequest(
                    afterSeq = 0L,
                    waitMs = 0L,
                    limit = 20,
                ),
            )

        assertTrue(result.events.isEmpty())
        assertFalse(result.needResync)
        assertEquals(2L, result.latestSeq)
    }

    @Test
    fun resetAdvancesVisibleLatestSeqEvenWhenNoEventsReturned() {
        val buffer = DeviceEventBuffer(capacity = 8, timestampProvider = { "2026-03-15T00:00:00Z" })
        buffer.publish(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
        )

        val before = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))
        buffer.reset()
        val after = buffer.poll(EventPollRequest(afterSeq = before.latestSeq, waitMs = 0L, limit = 20))

        assertTrue(after.events.isEmpty())
        assertTrue(after.needResync)
        assertTrue(after.latestSeq > before.latestSeq)
    }

    @Test
    fun resetMarksPreResetCursorAsNeedingResync() {
        val buffer = DeviceEventBuffer(capacity = 8, timestampProvider = { "2026-03-15T00:00:00Z" })
        buffer.publish(
            RuntimeStatusPayload(
                serverRunning = false,
                accessibilityEnabled = false,
                accessibilityConnected = false,
                runtimeReady = false,
            ),
        )
        buffer.publish(
            PackageChangedPayload(
                packageName = "com.android.settings",
                activityName = null,
            ),
        )

        buffer.reset()

        val result =
            buffer.poll(
                EventPollRequest(
                    afterSeq = 2L,
                    waitMs = 0L,
                    limit = 20,
                ),
            )

        assertTrue(result.events.isEmpty())
        assertTrue(result.needResync)
        assertEquals(3L, result.latestSeq)
    }

    @Test
    fun publishesMonotonicEventSequencesAfterReset() {
        val buffer = DeviceEventBuffer(capacity = 8, timestampProvider = { "2026-03-15T00:00:00Z" })
        buffer.publish(
            RuntimeStatusPayload(
                serverRunning = false,
                accessibilityEnabled = false,
                accessibilityConnected = false,
                runtimeReady = false,
            ),
        )

        buffer.reset()
        buffer.publish(
            PackageChangedPayload(
                packageName = "com.android.settings",
                activityName = null,
            ),
        )

        val result =
            buffer.poll(
                EventPollRequest(
                    afterSeq = 0L,
                    waitMs = 0L,
                    limit = 20,
                ),
            )

        assertEquals(listOf(3L), result.events.map(DeviceEvent::seq))
        assertEquals(3L, result.latestSeq)
        assertFalse(result.needResync)
    }

    private fun assertNullReasonRejected(
        payloadClass: Class<*>,
        vararg arguments: Any?,
    ) {
        val constructor = payloadClass.declaredConstructors.single()
        try {
            constructor.newInstance(*arguments)
            fail("expected ${payloadClass.simpleName} to reject null reason")
        } catch (_: InvocationTargetException) {
        }
    }
}

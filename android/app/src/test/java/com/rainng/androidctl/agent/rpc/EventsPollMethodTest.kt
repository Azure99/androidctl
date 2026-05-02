package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.events.DeviceEvent
import com.rainng.androidctl.agent.events.EventPollRequest
import com.rainng.androidctl.agent.events.EventPollResult
import com.rainng.androidctl.agent.events.ImeChangedPayload
import com.rainng.androidctl.agent.events.SnapshotInvalidatedPayload
import com.rainng.androidctl.agent.events.WindowChangedPayload
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class EventsPollMethodTest {
    @Test
    fun policyIsMetadataOnlyForPreparedFlow() {
        val method = EventsPollMethod(eventsPollProvider = { emptyPollResult() })

        assertFalse(method.policy.requiresReadyRuntime)
        assertEquals("INTERNAL_ERROR", method.policy.timeoutError.name)
        assertEquals("events.poll timed out", method.policy.timeoutMessage)
    }

    @Test
    fun prepareReusesDecodedRequestForTimeoutAndExecutionAndEncodesTypedResultAtBoundary() {
        var capturedRequest: EventPollRequest? = null
        val payload =
            EventPollResult(
                events =
                    listOf(
                        DeviceEvent(
                            seq = 6L,
                            timestamp = "2026-03-27T00:00:00Z",
                            data = ImeChangedPayload(visible = false, windowId = null),
                        ),
                        DeviceEvent(
                            seq = 7L,
                            timestamp = "2026-03-27T00:00:01Z",
                            data = SnapshotInvalidatedPayload(packageName = null, reason = "viewScrolled"),
                        ),
                    ),
                latestSeq = 7L,
                needResync = true,
                timedOut = false,
            )
        val method =
            EventsPollMethod(eventsPollProvider = { request ->
                capturedRequest = request
                payload
            })
        val request = request("""{"afterSeq":5,"waitMs":250,"limit":7}""")
        val prepared = method.prepare(request)
        val encoded = prepared.executeEncoded()

        assertEquals(250L + RequestBudgets.EVENTS_POLL_GRACE_MS, prepared.timeoutMs)
        assertEquals(EventPollRequest(afterSeq = 5L, waitMs = 250L, limit = 7), capturedRequest)
        assertEquals(7L, encoded.getLong("latestSeq"))
        assertTrue(encoded.getBoolean("needResync"))
        assertFalse(encoded.getBoolean("timedOut"))
        val events = encoded.getJSONArray("events")
        assertEquals(2, events.length())
        assertTrue(events.getJSONObject(0).getJSONObject("data").isNull("windowId"))
        assertTrue(events.getJSONObject(1).getJSONObject("data").isNull("packageName"))
    }

    @Test
    fun prepareKeepsEventsPollPayloadCoarseAndRawOnly() {
        val method =
            EventsPollMethod(
                eventsPollProvider = {
                    EventPollResult(
                        events =
                            listOf(
                                DeviceEvent(
                                    seq = 3L,
                                    timestamp = "2026-03-27T00:00:00Z",
                                    data =
                                        WindowChangedPayload(
                                            packageName = "com.android.settings",
                                            activityName = "SettingsActivity",
                                            reason = "windowStateChanged",
                                        ),
                                ),
                                DeviceEvent(
                                    seq = 4L,
                                    timestamp = "2026-03-27T00:00:01Z",
                                    data = SnapshotInvalidatedPayload(packageName = null, reason = "viewScrolled"),
                                ),
                            ),
                        latestSeq = 4L,
                        needResync = false,
                        timedOut = false,
                    )
                },
            )

        val encoded = method.prepare(request("""{"afterSeq":2,"waitMs":0,"limit":20}""")).executeEncoded()
        val encodedText = encoded.toString()

        assertFalse(encoded.has("screenId"))
        assertFalse(encoded.has("continuityStatus"))
        assertFalse(encodedText.contains("\"blockingGroup\""))
        assertFalse(encodedText.contains("\"screenId\""))
        assertFalse(encodedText.contains("\"continuityStatus\""))
        assertFalse(encodedText.contains("\"ref\""))
    }

    @Test
    fun prepareRejectsInvalidParams() {
        val method = EventsPollMethod(eventsPollProvider = { emptyPollResult() })

        try {
            method.prepare(request("""{"afterSeq":-1,"waitMs":0,"limit":20}"""))
            fail("expected RequestValidationException")
        } catch (error: RequestValidationException) {
            assertEquals("events.poll requires afterSeq >= 0", error.message)
        }
    }

    @Test
    fun preparePreservesTimeoutAsEmptyEventsPayload() {
        val method = EventsPollMethod(eventsPollProvider = { emptyPollResult() })

        val encoded = method.prepare(request("""{"afterSeq":0,"waitMs":250,"limit":20}""")).executeEncoded()

        assertTrue(encoded.getBoolean("timedOut"))
        assertFalse(encoded.getBoolean("needResync"))
        assertEquals(0, encoded.getJSONArray("events").length())
        assertEquals(0L, encoded.getLong("latestSeq"))
    }

    private fun request(params: String): RpcRequestEnvelope =
        RpcRequestEnvelope(
            id = "req-events",
            method = "events.poll",
            params = JSONObject(params),
        )

    private fun emptyPollResult(): EventPollResult =
        EventPollResult(
            events = emptyList(),
            latestSeq = 0L,
            needResync = false,
            timedOut = true,
        )
}

package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.MetaGetMethod
import com.rainng.androidctl.agent.rpc.RpcRequestEnvelope
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ActionRegistryConformanceTest {
    @Test
    fun advertisedActionKindsExactlyMatchDecodableAndRecordedExecutionKinds() {
        val advertisedKinds = advertisedActionKinds()
        val sampleRequestsByKind = requestSamplesByKind()
        val decodableKinds = sampleRequestsByKind.values.map { params -> decodeActionRequest(params).kind.wireName }.toSet()
        val backend = RecordingActionBackend()
        val dispatcher = ActionRequestDispatcher(backend)

        sampleRequestsByKind.values.forEach { params ->
            dispatcher.dispatch(decodeActionRequest(params))
        }

        assertEquals(advertisedKinds.toSet(), decodableKinds)
        assertEquals(advertisedKinds.toSet(), backend.recordedExecutedKinds())
        assertEquals(expectedInvocations(), backend.invocations)
        assertEquals(advertisedKinds, ActionKind.capabilityWireNames())
    }

    @Test
    fun nonAdvertisedAndAliasLikeActionKindsAreRejected() {
        val advertisedKinds = advertisedActionKinds().toSet()
        val rejectedKinds =
            listOf(
                "Tap",
                "tap ",
                "click",
                "longtap",
                "launch_app",
                "openURL",
            )

        rejectedKinds.forEach { kind ->
            assertFalse(advertisedKinds.contains(kind))
            assertInvalidRequest("unsupported action kind '$kind'") {
                decodeActionRequest(actionRequestJson(kind = kind, target = ActionTarget.None))
            }
        }
    }

    @Test
    fun decodeRequestPreservesValidationFailureAsCause() {
        try {
            decodeActionRequest(
                actionRequestJson(
                    kind = "type",
                    target = ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.2"),
                    input =
                        JSONObject()
                            .put("text", JSONObject.NULL)
                            .put("replace", true)
                            .put("submit", false)
                            .put("ensureFocused", true),
                ),
            )
            fail("expected ActionException")
        } catch (error: ActionException) {
            assertEquals("type requires text string", error.message)
            assertTrue(error.cause is RequestValidationException)
        }
    }

    private fun advertisedActionKinds(): List<String> {
        val result =
            MetaGetMethod(versionProvider = { "1.0.0" })
                .prepare(RpcRequestEnvelope(id = "req-meta", method = "meta.get", params = JSONObject()))
                .executeEncoded()
        return jsonArrayStrings(result.getJSONObject("capabilities").getJSONArray("actionKinds"))
    }

    private fun requestSamplesByKind(): Map<String, JSONObject> =
        linkedMapOf(
            "tap" to
                actionRequestJson(
                    kind = "tap",
                    target = ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.1"),
                ),
            "longTap" to
                actionRequestJson(
                    kind = "longTap",
                    target = ActionTarget.Coordinates(x = 540f, y = 1200f),
                ),
            "type" to
                actionRequestJson(
                    kind = "type",
                    target = ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.2"),
                    input =
                        JSONObject()
                            .put("text", "wifi")
                            .put("replace", true)
                            .put("submit", false)
                            .put("ensureFocused", true),
                ),
            "node" to
                actionRequestJson(
                    kind = "node",
                    target = ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.3"),
                    node = JSONObject().put("action", "focus"),
                ),
            "scroll" to
                actionRequestJson(
                    kind = "scroll",
                    target = ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.4"),
                    scroll = JSONObject().put("direction", "down"),
                ),
            "global" to
                actionRequestJson(
                    kind = "global",
                    target = ActionTarget.None,
                    global = JSONObject().put("action", "back"),
                ),
            "gesture" to
                actionRequestJson(
                    kind = "gesture",
                    target = ActionTarget.None,
                    gesture = JSONObject().put("direction", "down"),
                ),
            "launchApp" to
                actionRequestJson(
                    kind = "launchApp",
                    target = ActionTarget.None,
                    intent = JSONObject().put("packageName", "com.android.settings"),
                ),
            "openUrl" to
                actionRequestJson(
                    kind = "openUrl",
                    target = ActionTarget.None,
                    intent = JSONObject().put("url", "https://example.com"),
                ),
        )

    private fun expectedInvocations(): List<RecordedActionInvocation> =
        listOf(
            RecordedActionInvocation(kind = "tap", detail = "handle:42:w1:0.1"),
            RecordedActionInvocation(kind = "longTap", detail = "coordinates:540.0:1200.0:5000"),
            RecordedActionInvocation(kind = "type", detail = "handle:42:w1:0.2:wifi:true:false:true:5000"),
            RecordedActionInvocation(kind = "node", detail = "handle:42:w1:0.3:focus"),
            RecordedActionInvocation(kind = "scroll", detail = "handle:42:w1:0.4:down"),
            RecordedActionInvocation(kind = "global", detail = "back"),
            RecordedActionInvocation(kind = "gesture", detail = "down:5000"),
            RecordedActionInvocation(kind = "launchApp", detail = "com.android.settings"),
            RecordedActionInvocation(kind = "openUrl", detail = "https://example.com"),
        )

    private fun jsonArrayStrings(array: JSONArray): List<String> = List(array.length(), array::getString)

    private fun assertInvalidRequest(
        expectedMessage: String,
        block: () -> Unit,
    ) {
        try {
            block()
            fail("expected ActionException")
        } catch (error: ActionException) {
            assertEquals(expectedMessage, error.message)
            assertEquals(false, error.retryable)
        }
    }
}

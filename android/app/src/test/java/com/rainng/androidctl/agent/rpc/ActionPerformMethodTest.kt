package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.actions.ActionKind
import com.rainng.androidctl.agent.actions.ActionRequest
import com.rainng.androidctl.agent.actions.ActionResult
import com.rainng.androidctl.agent.actions.ActionResultStatus
import com.rainng.androidctl.agent.actions.ActionTarget
import com.rainng.androidctl.agent.actions.TapActionRequest
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ActionPerformMethodTest {
    @Test
    fun prepareUsesActionTimeoutBudgetAndDelegatesTypedRequest() {
        var capturedRequest: ActionRequest? = null
        val payload =
            ActionResult(
                actionId = "action-42",
                status = ActionResultStatus.Done,
                durationMs = 17L,
                resolvedTarget = ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.1"),
                observed = ObservedWindowState(packageName = "com.android.settings", activityName = null),
            )
        val request =
            request(
                """{"kind":"tap","target":{"kind":"handle","handle":{"snapshotId":42,"rid":"w1:0.1"}},"options":{"timeoutMs":1200}}""",
            )
        val method =
            ActionPerformMethod(
                actionExecutionFactory =
                    providerFactory { typedRequest ->
                        capturedRequest = typedRequest
                        payload
                    },
            )
        val prepared = method.prepare(request)

        assertEquals(true, method.policy.requiresReadyRuntime)
        assertEquals(true, method.policy.requiresAccessibilityHandle)
        assertEquals(1200L + RequestBudgets.ACTION_TIMEOUT_GRACE_MS, prepared.timeoutMs)
        assertEquals("ACTION_TIMEOUT", method.policy.timeoutError.name)
        assertEquals("action.perform timed out", method.policy.timeoutMessage)
        assertNull(capturedRequest)
        val encoded = prepared.executeEncoded()
        assertTrue(capturedRequest is TapActionRequest)
        assertEquals(ActionKind.Tap, capturedRequest?.kind)
        assertEquals(ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.1"), capturedRequest?.target)
        assertEquals("action-42", encoded.getString("actionId"))
        assertEquals("done", encoded.getString("status"))
        assertEquals(17L, encoded.getLong("durationMs"))
        assertEquals("handle", encoded.getJSONObject("resolvedTarget").getString("kind"))
        assertTrue(encoded.getJSONObject("observed").has("activityName"))
        assertTrue(encoded.getJSONObject("observed").isNull("activityName"))
    }

    @Test
    fun prepareKeepsActionPerformResponseRawOnlyWithoutSemanticScreenFields() {
        val payload =
            ActionPerformMethod(
                actionExecutionFactory =
                    providerFactory {
                        ActionResult(
                            actionId = "action-42",
                            status = ActionResultStatus.Done,
                            durationMs = 17L,
                            resolvedTarget = ActionTarget.None,
                            observed = ObservedWindowState(packageName = "com.android.settings", activityName = null),
                        )
                    },
            ).prepare(
                request("""{"kind":"global","target":{"kind":"none"},"global":{"action":"home"},"options":{"timeoutMs":5000}}"""),
            ).executeEncoded()

        assertEquals("action-42", payload.getString("actionId"))
        assertFalse(payload.has("screenId"))
        assertFalse(payload.has("nextScreenId"))
        assertFalse(payload.has("continuityStatus"))
        assertFalse(payload.has("blockingGroup"))
        assertFalse(payload.getJSONObject("observed").has("screenId"))
        assertFalse(payload.getJSONObject("observed").has("ref"))
    }

    @Test
    fun prepareEncodesPartialStatusAsRawWireString() {
        val requestBody =
            """{"kind":"type","target":{"kind":"handle","handle":{"snapshotId":42,"rid":"w1:0.1"}}""" +
                ""","input":{"text":"wifi","replace":true,"submit":false,"ensureFocused":true}""" +
                ""","options":{"timeoutMs":5000}}"""
        val payload =
            ActionPerformMethod(
                actionExecutionFactory =
                    providerFactory {
                        ActionResult(
                            actionId = "action-43",
                            status = ActionResultStatus.Partial,
                            durationMs = 19L,
                            resolvedTarget = ActionTarget.None,
                            observed =
                                ObservedWindowState(
                                    packageName = "com.android.settings",
                                    activityName = null,
                                ),
                        )
                    },
            ).prepare(
                request(requestBody),
            ).executeEncoded()

        assertEquals("partial", payload.getString("status"))
    }

    @Test
    fun prepareRejectsNonObjectOptions() {
        val method =
            ActionPerformMethod(
                actionExecutionFactory =
                    providerFactory {
                        ActionResult(
                            actionId = "action-1",
                            status = ActionResultStatus.Done,
                            durationMs = 1L,
                            resolvedTarget = ActionTarget.None,
                            observed = ObservedWindowState(),
                        )
                    },
            )
        val request = request("""{"kind":"tap","target":{"kind":"none"},"options":"slow"}""")

        try {
            method.prepare(request)
        } catch (error: RequestValidationException) {
            assertEquals("options must be a JSON object", error.message)
            return
        }

        throw AssertionError("expected RequestValidationException")
    }

    @Test
    fun prepareRejectsMissingOptions() {
        val method =
            ActionPerformMethod(
                actionExecutionFactory =
                    providerFactory {
                        ActionResult(
                            actionId = "action-1",
                            status = ActionResultStatus.Done,
                            durationMs = 1L,
                            resolvedTarget = ActionTarget.None,
                            observed = ObservedWindowState(),
                        )
                    },
            )

        try {
            method.prepare(request("""{"kind":"tap","target":{"kind":"coordinates","x":100,"y":200}}"""))
        } catch (error: RequestValidationException) {
            assertEquals("action.perform requires options", error.message)
            return
        }

        throw AssertionError("expected RequestValidationException")
    }

    @Test
    fun prepareBindsActionExecutionFactoryBeforeExecute() {
        var capturedLabel = "action-prepare"
        val method =
            ActionPerformMethod {
                val boundLabel = capturedLabel
                {
                    ActionResult(
                        actionId = boundLabel,
                        status = ActionResultStatus.Done,
                        durationMs = 1L,
                        resolvedTarget = ActionTarget.None,
                        observed = ObservedWindowState(),
                    )
                }
            }

        val prepared =
            method.prepare(
                request(
                    """{"kind":"tap","target":{"kind":"handle","handle":{"snapshotId":42,"rid":"w1:0.1"}},"options":{"timeoutMs":5000}}""",
                ),
            )
        capturedLabel = "action-execute"
        val encoded = prepared.executeEncoded()

        assertEquals("action-prepare", encoded.getString("actionId"))
    }

    private fun request(params: String): RpcRequestEnvelope =
        RpcRequestEnvelope(
            id = "req-action",
            method = "action.perform",
            params = JSONObject(params),
        )

    private fun providerFactory(provider: (ActionRequest) -> ActionResult): (ActionRequest) -> () -> ActionResult =
        { request -> { provider(request) } }
}

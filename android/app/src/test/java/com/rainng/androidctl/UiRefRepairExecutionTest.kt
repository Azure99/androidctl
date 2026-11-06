package com.rainng.androidctl

import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Test

class UiRefRepairExecutionTest {
    private class Request {
        var now = 0L
        val budget = UiBudget(5000) { now }
        val run =
            UiRun(
                "click",
                Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS),
                json("ref" to "e1"),
                budget,
            )
        val snapshots = UiSnapshot()

        init {
            install("e1")
        }

        fun install(ref: String): JSONObject {
            val facts = node(ref, "Field").put("refAvailable", true)
            UiSemantics.project(listOf(facts))
            val tree = json("sequence" to 1, "partial" to false, "nodes" to JSONArray(listOf(facts)))
            // The repair orchestration only passes the handle through to the action callback.
            val target = UiTarget(AccessibilityNodeInfo(), facts)
            val collector = TreeCollector(emptyList(), json(), budget)
            snapshots.install(SnapshotAssembly(tree, mapOf(ref to target), collector, changed = false))
            return tree
        }
    }

    private fun rejection(code: String = "selectionChanged"): JSONObject =
        outcome("click", Outcome.NOT_SENT).put("error", json("code" to code))

    @Test fun returnedOutcomesOtherThanNotSentNeverRepair() {
        for (result in listOf(Outcome.UNKNOWN, Outcome.FAILED, Outcome.DONE)) {
            val request = Request()
            val first = rejection().put("result", result)
            var attempts = 0
            val answer =
                UiRefRepair.perform(request.snapshots, request.run, { error("$result must not trigger capture") }) { _, _ ->
                    attempts++
                    first
                }
            assertEquals(structure(first), structure(answer))
            assertEquals(1, attempts)
        }
    }

    @Test fun aRejectionNeedsARepairableCode() {
        for (code in listOf(null, "arguments", "notReady")) {
            val request = Request()
            val first = outcome("click", Outcome.NOT_SENT)
            if (code != null) first.put("error", json("code" to code))
            val answer = UiRefRepair.perform(request.snapshots, request.run, { error("$code must not trigger capture") }) { _, _ -> first }
            assertEquals(structure(first), structure(answer))
        }
    }

    @Test fun dispatchedReturnAndExceptionNeverRepair() {
        for (throwAfterDispatch in listOf(false, true)) {
            val request = Request()
            val first = rejection()
            val error = Rejected("selectionChanged", "Rejected after dispatch")
            val pending = outcome("click", Outcome.UNKNOWN)
            var attempts = 0
            val perform = {
                UiRefRepair.perform(request.snapshots, request.run, { error("A dispatched action must not trigger capture") }) { _, _ ->
                    attempts++
                    request.run.begin(pending)
                    if (throwAfterDispatch) throw error
                    first
                }
            }
            if (throwAfterDispatch) {
                assertSame(error, assertThrows(Rejected::class.java) { perform() })
            } else {
                assertEquals(structure(first), structure(perform()))
            }
            assertEquals(1, attempts)
            assertEquals(Outcome.UNKNOWN, request.run.failure(error).getString("result"))
        }
    }

    @Test fun aReturnedRejectionCannotStartRepairAfterItsBudgetExpires() {
        val request = Request()
        val first = rejection()
        val answer =
            UiRefRepair.perform(request.snapshots, request.run, { error("An exhausted budget must not trigger capture") }) { _, _ ->
                request.now = request.budget.deadline
                first
            }
        assertEquals(structure(first), structure(answer))
    }

    @Test fun aReplacementRejectionEndsTheOnlyRepairAndClearsOriginalFacts() {
        for (code in listOf("staleRef", "selectionChanged", "identityUnknown")) {
            val request = Request()
            val events = mutableListOf<String>()
            val answer =
                UiRefRepair.perform(request.snapshots, request.run, {
                    events.add("capture")
                    request.install("e2")
                }) { target, method ->
                    events.add("$method:${target.facts.getString("ref")}")
                    if (method == "original") {
                        request.run.resolve(method, target.facts)
                        request.run.select("click", target.facts)
                        throw Rejected(code, "Original target changed")
                    }
                    request.run.failure(Rejected(code, "Replacement also changed"))
                }
            assertEquals(listOf("original:e1", "capture", "signature:e2"), events)
            assertEquals("Replacement also changed", answer.getString("reason"))
            assertEquals(code, answer.getJSONObject("error").getString("code"))
            assertFalse(answer.getJSONObject("data").has("resolved"))
            assertFalse(answer.getJSONObject("data").has("selected"))
        }
    }

    @Test fun aRepairSelectionFailureDoesNotRetryAndReportsTheRecoveryError() {
        val request = Request()
        var attempts = 0
        val failure =
            assertThrows(Rejected::class.java) {
                UiRefRepair.perform(request.snapshots, request.run, {
                    json("partial" to true, "nodes" to JSONArray())
                }) { _, _ ->
                    attempts++
                    rejection()
                }
            }
        val answer = request.run.failure(failure)
        assertEquals(1, attempts)
        assertEquals(Outcome.NOT_SENT, answer.getString("result"))
        assertEquals("identityUnknown", answer.getJSONObject("error").getString("code"))
        assertFalse(request.run.dispatchStarted)
    }

    @Test fun responseDuringCaptureKeepsTheFirstFactAndPreventsReplacementDispatch() {
        val request = Request()
        var returned: JSONObject? = null
        val failure =
            assertThrows(Rejected::class.java) {
                UiRefRepair.perform(request.snapshots, request.run, {
                    returned = request.run.response(expired = true)
                    request.install("e2")
                }) { _, method ->
                    if (method == "original") {
                        rejection()
                    } else {
                        request.run.begin(null)
                        error("The replacement dispatched after the response")
                    }
                }
            }
        assertEquals("requestEnded", failure.code)
        assertFalse(request.run.dispatchStarted)
        val response = requireNotNull(returned)
        assertEquals(Outcome.NOT_SENT, response.getString("result"))
        assertEquals("selectionChanged", response.getJSONObject("error").getString("code"))
        assertFalse(response.getJSONObject("data").getBoolean("dispatchStarted"))
    }

    @Test fun responseAfterReplacementSelectionHasNoOriginalFactAndPreventsDispatch() {
        val request = Request()
        var returned: JSONObject? = null
        val failure =
            assertThrows(Rejected::class.java) {
                UiRefRepair.perform(request.snapshots, request.run, { request.install("e2") }) { _, method ->
                    if (method == "original") {
                        rejection()
                    } else {
                        returned = request.run.response(expired = true)
                        request.run.begin(null)
                        error("The replacement dispatched after the response")
                    }
                }
            }
        assertEquals("requestEnded", failure.code)
        assertFalse(request.run.dispatchStarted)
        val response = requireNotNull(returned)
        assertEquals(Outcome.NOT_SENT, response.getString("result"))
        assertFalse(response.has("error"))
    }
}

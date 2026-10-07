package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class UiConditionTest {
    @Test fun goneRequiresACompleteTreeWithNoTrueOrUnknownSelectorMatches() {
        val condition = UiCondition(json("gone" to true, "selector" to json("text" to "Saving")))
        val target = node("e1", "Saving")
        var evaluation = condition.evaluate(tree(target))
        assertFalse(evaluation.matched)
        assertEquals(1, evaluation.predicate!!.getInt("matches"))
        target.getJSONArray("unavailable").put(json("field" to "text", "reason" to "truncated"))
        evaluation = condition.evaluate(tree(target))
        assertFalse(evaluation.matched)
        assertEquals(0, evaluation.predicate!!.getInt("matches"))
        assertEquals(1, evaluation.predicate.getInt("unknown"))
        val differentRole = UiCondition(json("gone" to true, "selector" to json("role" to "button", "text" to "Saving")))
        assertTrue(differentRole.evaluate(tree(target)).matched)
        target.put("visible", false)
        evaluation = condition.evaluate(tree(target))
        assertTrue(evaluation.matched)
        assertEquals(0, evaluation.predicate!!.getInt("unknown"))
        evaluation = condition.evaluate(tree().put("partial", true))
        assertFalse(evaluation.matched)
        assertFalse(evaluation.predicate!!.getBoolean("complete"))
        evaluation = condition.evaluate(tree())
        assertTrue(evaluation.matched)
        assertTrue(evaluation.predicate!!.getBoolean("complete"))
    }

    @Test fun idleUsesConsecutiveCompleteNonLoadingSamplesAndResetsAfterMissing() {
        val condition = UiCondition(json("idle" to true))
        val target = node("e1", "Ready")
        assertFalse(condition.evaluate(tree(target)).matched)
        condition.missing("Collection failed")
        assertFalse(condition.evaluate(tree(target).put("sequence", 2)).matched)
        var evaluation = condition.evaluate(tree(target).put("sequence", 3))
        assertTrue(evaluation.matched)
        assertEquals(3L, evaluation.predicate!!.getLong("sequence"))
        assertTrue(evaluation.predicate.getBoolean("stable"))
        evaluation = condition.evaluate(tree(target).put("partial", true))
        assertFalse(evaluation.matched)
        assertFalse(evaluation.predicate!!.getBoolean("complete"))
        assertFalse(evaluation.predicate.getBoolean("stable"))
        target.put("className", "android.widget.ProgressBar")
        repeat(2) {
            evaluation = condition.evaluate(tree(target))
            assertFalse(evaluation.matched)
        }
        assertTrue(evaluation.predicate.getBoolean("loading"))
        target.put("className", "android.widget.TextView")
        assertFalse(condition.evaluate(tree(target)).matched)
        assertTrue(condition.evaluate(tree(target)).matched)
    }

    @Test fun conditionsRejectConflictsAndSelectorsOutsideGoneAtTheDeviceBoundary() {
        for (args in listOf(
            json("gone" to true, "idle" to true, "selector" to json("text" to "Saving")),
            json("app" to "fixture", "gone" to true),
            json("gone" to true),
            json("gone" to true, "selector" to JSONObject()),
            json("gone" to true, "selector" to json("unknown" to "Saving")),
            json("idle" to true, "selector" to json("text" to "Saving")),
            json("app" to "fixture", "selector" to json("text" to "Saving")),
            json("idle" to false),
            json("screenChange" to true, "idle" to true),
            json("screenChange" to true, "selector" to json("text" to "Saving")),
            json("screenChange" to false),
        )) {
            val error = assertThrows(Rejected::class.java) { UiCondition(args) }
            assertEquals("condition", error.code)
            val run = UiRun("wait", Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS), args, UiBudget(5000) { 0 })
            assertEquals(Outcome.NOT_SENT, run.failure(error).getString("result"))
        }
    }

    @Test fun publishedChangeRequiresACompleteChangedSampleAndComparesSelectionPolicy() {
        for (selection in listOf(JSONObject(), json("package" to "fixture"), json("window" to 1))) {
            val sessions = Sessions({ 0L }, {})
            val delivery = UiDelivery(sessions, "worker")
            val lease = sessions.acquire(SessionOwner("pair", "task", "host"))
            val before =
                tree(
                    node("e1", "Before"),
                ).put("selectionScope", selection).put("scope", json("window" to 1, "package" to "fixture"))
            delivery.createCapture(lease, UiDelivery.prepareTree(before), false)
            val baseline = delivery.publishedBaseline(lease, before)
            val condition = UiCondition(json("screenChange" to true))
            condition.published(baseline, copy(selection))
            assertFalse(condition.evaluate(copy(before).put("sequence", 2)).matched)
            val after =
                tree(node("e1", "After"))
                    .put("sequence", 3)
                    .put(
                        "scope",
                        json(
                            "window" to if (selection.has("window")) 1 else 2,
                            "package" to if (selection.length() == 0) "other" else "fixture",
                        ),
                    )
            val partial = condition.evaluate(copy(after).put("partial", true))
            assertFalse(partial.matched)
            assertFalse(partial.predicate!!.getBoolean("complete"))
            condition.missing("Collection failed")
            val changed = condition.evaluate(after)
            assertTrue(changed.matched)
            assertEquals(1, changed.predicate!!.getJSONObject("scope").getInt("window"))
            assertEquals(if (selection.has("window")) 1 else 2, changed.predicate.getJSONObject("currentScope").getInt("window"))
            val mismatch =
                if (selection.has("window")) {
                    json("window" to 2)
                } else if (selection.has("package")) {
                    json("package" to "other")
                } else {
                    json(
                        "window" to 1,
                    )
                }
            val error = assertThrows(Rejected::class.java) { UiCondition(json("screenChange" to true)).published(baseline, mismatch) }
            assertEquals("scopeMismatch", error.code)
        }
    }

    @Test fun waitFailureKeepsLastPredicateAndOnlyRetriesTransientSamplingErrors() {
        val wait = UiWait(json("idle" to true))
        val sample = tree(node("e1", "Ready"))
        wait.observe(sample)
        val matched = wait.observe(copy(sample).put("sequence", 2))
        assertEquals(Outcome.DONE, matched.getString("result"))
        assertEquals("", matched.getString("reason"))
        for (code in listOf("scopeUnknown", "snapshotBudget", "timeout")) {
            val failed = wait.observationFailed(Rejected(code, "Unavailable"))
            assertTrue(failed.retryable)
            assertEquals(Outcome.FAILED, failed.result.getString("result"))
            assertEquals("Observation failed: Unavailable", failed.result.getString("reason"))
            assertFalse(failed.result.has("error"))
            assertEquals(
                2L,
                failed.result
                    .getJSONObject("data")
                    .getJSONObject("predicate")
                    .getLong("sequence"),
            )
        }
        assertEquals(Outcome.FAILED, wait.observe(copy(sample).put("sequence", 3)).getString("result"))
        for (error in listOf(Rejected("session", "Session ended"), IllegalStateException("Collector failed"))) {
            val failed = wait.observationFailed(error)
            assertFalse(failed.retryable)
            assertEquals(message(error), failed.result.getString("reason"))
            assertEquals(problem(error).toString(), failed.result.getJSONObject("error").toString())
        }
    }

    @Test fun waitOwnsPublishedBaselineRejections() {
        val sessions = Sessions({ 0L }, {})
        val lease = sessions.acquire(SessionOwner("pair", "task", "host"))
        val delivery = UiDelivery(sessions, "worker")
        val args = json("screenChange" to true)
        val missing = UiWait(args).start(delivery, lease, null)
        assertEquals(Outcome.NOT_SENT, missing.getString("result"))
        assertEquals("noPublishedSnapshot", missing.getJSONObject("error").getString("code"))
        val partial = tree(node("e1", "Before")).put("partial", true).put("selectionScope", JSONObject())
        delivery.createCapture(lease, UiDelivery.prepareTree(partial), false)
        val rejected = UiWait(args).start(delivery, lease, partial)
        assertEquals(Outcome.NOT_SENT, rejected.getString("result"))
        assertEquals("partialBaseline", rejected.getJSONObject("error").getString("code"))
    }

    private fun tree(vararg nodes: JSONObject): JSONObject {
        UiSemantics.project(nodes.toList())
        return json(
            "partial" to false,
            "sequence" to 1,
            "connection" to 1,
            "scope" to json("window" to 1),
            "windows" to JSONArray(),
            "nodes" to JSONArray(nodes.toList()),
        )
    }
}

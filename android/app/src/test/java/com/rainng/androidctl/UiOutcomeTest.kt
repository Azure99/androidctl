package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.InvocationTargetException

class UiOutcomeTest {
    @Test fun recordingAndProjectingActionFactsOwnIndependentCopies() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        val run = UiRun("click", lease, JSONObject(), UiBudget(5000) { 0 })
        val observationError = IllegalStateException("Post-action observation failed")
        val target = node("e7", "Search")
        UiSemantics.project(listOf(target))
        run.resolve("original", target)
        run.select("click", target)
        val pending = outcome("click", Outcome.UNKNOWN, "Platform action result pending")
        val pendingBefore = pending.toString()
        run.begin(pending)
        assertEquals(pendingBefore, pending.toString())
        pending.put("reason", "Caller changed pending")
        assertEquals("Platform action result pending", run.failure(observationError).getString("reason"))

        val completed = outcome("click", reason = "Platform returned")
        val completedBefore = completed.toString()
        run.remember(completed)
        assertEquals(completedBefore, completed.toString())
        completed.put("reason", "Caller changed result")
        val known = run.failure(observationError)
        known.getJSONObject("data").put("dispatchStarted", false)
        known.put("reason", "Caller changed known")
        val fresh = node("e8", "Fresh target")
        UiSemantics.project(listOf(fresh))
        run.resolve("signature", fresh)
        run.select("long-click", fresh)
        val failed = run.failure(observationError)
        assertEquals(
            "e8",
            failed
                .getJSONObject("data")
                .getJSONObject("resolved")
                .getJSONObject("target")
                .getString("ref"),
        )
        assertEquals(
            "e8",
            failed
                .getJSONObject("data")
                .getJSONObject("selected")
                .getJSONObject("target")
                .getString("ref"),
        )
        val response = run.response()
        assertEquals("Platform returned", response.getString("reason"))
        assertTrue(response.getJSONObject("data").getBoolean("dispatchStarted"))
        assertFalse(response.getJSONObject("data").has("sent"))
        assertEquals("long-click", response.getJSONObject("data").getJSONObject("selected").getString("action"))
        assertEquals(
            "e8",
            response
                .getJSONObject("data")
                .getJSONObject("resolved")
                .getJSONObject("target")
                .getString("ref"),
        )
        assertEquals(
            "e7",
            known
                .getJSONObject("data")
                .getJSONObject("resolved")
                .getJSONObject("target")
                .getString("ref"),
        )
        assertEquals("click", known.getJSONObject("data").getJSONObject("selected").getString("action"))
        val frozen = response.toString()
        run.remember(outcome("click", Outcome.FAILED, "Late local record"))
        run.select("click", target)
        assertEquals(frozen, response.toString())
        response.getJSONObject("data").put("dispatchStarted", false)
        assertTrue(run.failure(observationError).getJSONObject("data").getBoolean("dispatchStarted"))
    }

    @Test fun errorTextAndProblemPreserveOneWrapperAndEmptyMessages() {
        val samples =
            listOf(
                Rejected("condition", "Invalid condition") to ("condition" to "Invalid condition"),
                IllegalStateException() to ("workerFailure" to "IllegalStateException"),
                IllegalStateException("") to ("workerFailure" to ""),
                InvocationTargetException(Rejected("condition", "Invalid condition")) to ("condition" to "Invalid condition"),
                InvocationTargetException(InvocationTargetException(Rejected("condition", "Invalid condition"))) to
                    ("workerFailure" to "InvocationTargetException"),
            )
        for ((error, expected) in samples) {
            assertEquals(expected.second, message(error))
            assertEquals(expected.first, problem(error).getString("code"))
            assertEquals(expected.second, problem(error).getString("message"))
        }
    }

    @Test fun productionFailurePreservesKnownFactsAndAddsOnlyUnsentErrors() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        val error = Rejected("snapshotBudget", "Sampling ended")
        for ((command, expected) in listOf("click" to Outcome.NOT_SENT)) {
            val run = UiRun(command, lease, JSONObject(), UiBudget(5000) { 0 })
            val failed = run.failure(error)
            assertEquals(expected, failed.getString("result"))
            assertEquals("snapshotBudget", failed.getJSONObject("error").getString("code"))
        }
        for (state in listOf(Outcome.NOT_SENT, Outcome.DONE, Outcome.FAILED, Outcome.UNKNOWN)) {
            val run = UiRun("click", lease, JSONObject(), UiBudget(5000) { 0 })
            if (state != Outcome.NOT_SENT) run.begin(null)
            run.remember(outcome("click", state, "Known action", json("selected" to json("action" to "click"))))
            val failed = run.failure(error)
            assertEquals(state, failed.getString("result"))
            assertEquals(state != Outcome.NOT_SENT, failed.getJSONObject("data").getBoolean("dispatchStarted"))
            assertEquals("click", failed.getJSONObject("data").getJSONObject("selected").getString("action"))
            if (state == Outcome.NOT_SENT) {
                assertEquals("Sampling ended", failed.getString("reason"))
                assertEquals("snapshotBudget", failed.getJSONObject("error").getString("code"))
            } else {
                assertEquals("Known action", failed.getString("reason"))
                assertFalse(failed.has("error"))
            }
            failed.put("reason", "Caller changed failure")
            assertEquals("Known action", run.response().getString("reason"))
        }
    }

    @Test fun responseKeepsKnownFactsAndDistinguishesUnsentWaitFromInput() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        for ((command, expected) in listOf("click" to "not sent")) {
            val run = UiRun(command, lease, JSONObject(), UiBudget(5000) { 0 })
            val response = run.response()
            assertEquals(expected, response.getString("result"))
            assertEquals("requestEnded", assertThrows(Rejected::class.java) { run.begin(null) }.code)
        }
        val pending = UiRun("click", lease, JSONObject(), UiBudget(5000) { 0 })
        pending.begin(null)
        assertEquals("UNKNOWN", pending.response().getString("result"))
        for (state in listOf(Outcome.DONE, Outcome.FAILED)) {
            for (started in listOf(false, true)) {
                val known = UiRun("click", lease, json("screenshot" to true), UiBudget(5000) { 0 })
                if (started) known.begin(null)
                known.remember(outcome("click", state))
                val response = known.response(expired = true)
                assertEquals(state, response.getString("result"))
                val data = response.getJSONObject("data")
                assertEquals(started, data.getBoolean("dispatchStarted"))
                assertFalse(data.has("sent"))
                assertTrue(data.has("snapshotError"))
                assertTrue(data.has("imageError"))
            }
        }
    }

    @Test fun expiryPreservesPublishedTreeAndKnownActionWhileImageIsPending() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        val run = UiRun("click", lease, json("screenshot" to true), UiBudget(5000) { 0 })
        run.begin(null)
        run.remember(outcome("click", data = json("capture" to "worker:1", "platform" to json("accepted" to true))))

        val result = run.response(expired = true)
        val data = result.getJSONObject("data")
        assertEquals(Outcome.DONE, result.getString("result"))
        assertTrue(data.getBoolean("dispatchStarted"))
        assertTrue(data.getJSONObject("platform").getBoolean("accepted"))
        assertEquals("worker:1", data.getString("capture"))
        assertFalse(data.has("snapshotError"))
        assertTrue(data.has("imageError"))
    }

    @Test fun expiryPreservesSpecificSnapshotFailureWithOrWithoutPublishedTree() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        for (capture in listOf(null, "worker:1")) {
            val run = UiRun("click", lease, json(), UiBudget(5000) { 0 })
            val data = json("snapshotError" to "Window changed during observation")
            if (capture != null) data.put("capture", capture)
            run.remember(outcome("click", Outcome.FAILED, "Platform rejected the action", data))

            val result = run.response(expired = true)
            assertEquals(Outcome.FAILED, result.getString("result"))
            assertEquals("Platform rejected the action", result.getString("reason"))
            assertEquals("Window changed during observation", result.getJSONObject("data").getString("snapshotError"))
        }
    }

    @Test fun actionFactsKeepSelectionSeparateFromDispatchAcrossPendingAndExpiry() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        val original = node("e7", "Search")
        UiSemantics.project(listOf(original))
        val pending = UiRun("key", lease, json("ref" to "e7", "key" to "enter"), UiBudget(5000) { 0 })
        pending.resolve("original", original)
        pending.select("key", original, "enter")
        pending.begin(outcome("key", Outcome.UNKNOWN, "Platform key input pending"))
        val response = pending.response(expired = true)
        assertEquals("UNKNOWN", response.getString("result"))
        assertEquals("enter", response.getJSONObject("data").getJSONObject("selected").getString("key"))
        assertTrue(response.getJSONObject("data").getBoolean("dispatchStarted"))
        assertFalse(response.getJSONObject("data").has("sent"))
        assertTrue(response.getJSONObject("data").has("snapshotError"))
    }

    @Test fun globalAndCoordinateActionsKeepNullNodeTargetsAndReadOnlyResultsOmitActionFacts() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        for (command in listOf("home")) {
            val run = UiRun(command, lease, JSONObject(), UiBudget(5000) { 0 })
            run.select(command)
            run.begin(null)
            val data = run.response().getJSONObject("data")
            assertFalse(data.has("requested"))
            assertTrue(data.getJSONObject("selected").isNull("target"))
            assertFalse(data.has("sent"))
            assertTrue(data.getBoolean("dispatchStarted"))
            assertFalse(data.has("resolved"))
        }
    }

    @Test fun settlementNeedsConsecutiveCompleteSamplesAndKeepsFinalChangeSeparate() {
        val before = tree("A")
        val stable = SettledSamples(before)
        assertEquals(Settlement.UNSETTLED, stable.classification())
        val unavailable = stable.report(null)
        assertEquals("No post-action snapshot within the budget", unavailable.getString("snapshotError"))
        assertEquals("unknown", unavailable.getJSONObject("observedChange").getString("state"))
        stable.accept(copy(before))
        assertEquals(Settlement.UNSETTLED, stable.classification())
        stable.accept(copy(before))
        assertEquals(Settlement.UNCHANGED, stable.classification())
        stable.accept(copy(before).put("partial", true))
        assertEquals(Settlement.UNSETTLED, stable.classification())
        stable.accept(copy(before))
        assertEquals(Settlement.UNSETTLED, stable.classification())
        stable.missing("Collection failed")
        var report = stable.report(before)
        assertEquals("Collection failed", report.getString("snapshotError"))
        assertEquals("unchanged", report.getJSONObject("observedChange").getString("state"))
        report = stable.report(null)
        assertEquals("Collection failed", report.getString("snapshotError"))
        stable.accept(copy(before))
        assertEquals(Settlement.UNSETTLED, stable.classification())
        stable.accept(copy(before))
        assertEquals(Settlement.UNCHANGED, stable.classification())
        report = stable.report(before)
        assertFalse(report.has("snapshotError"))
        assertEquals("unsettled", stable.report(before, completeWindow = false).getString("settle"))

        val changed = SettledSamples(before)
        changed.accept(tree("B"))
        changed.accept(tree("B"))
        assertEquals(Settlement.CHANGED, changed.classification())
        changed.accept(copy(before))
        changed.accept(copy(before))
        assertEquals(Settlement.UNSETTLED, changed.classification())
        report = changed.report(before)
        assertEquals("unchanged", report.getJSONObject("observedChange").getString("state"))
        val partialBaseline = SettledSamples(copy(before).put("partial", true))
        repeat(2) { partialBaseline.accept(copy(before)) }
        assertEquals(Settlement.UNSETTLED, partialBaseline.classification())
    }

    @Test fun noBaselineStopsOnlyAfterTwoCompleteEqualNonLoadingSamples() {
        val samples = SettledSamples(null)
        val tree = tree("A")
        samples.accept(tree)
        assertFalse(samples.stableWithoutBaseline())
        samples.accept(tree)
        assertTrue(samples.stableWithoutBaseline())
        assertEquals(Settlement.UNSETTLED, samples.classification())
        val report = samples.report(tree)
        assertEquals("unknown", report.getJSONObject("observedChange").getString("state"))
        samples.missing("Collection failed")
        assertFalse(samples.stableWithoutBaseline())
        samples.accept(tree)
        assertFalse(samples.stableWithoutBaseline())
        samples.accept(tree)
        assertTrue(samples.stableWithoutBaseline())
        samples.accept(tree("B"))
        assertFalse(samples.stableWithoutBaseline())
        repeat(2) { samples.accept(copy(tree).put("partial", true)) }
        assertFalse(samples.stableWithoutBaseline())

        val loading = copy(tree)
        loading.getJSONArray("nodes").getJSONObject(0).put("className", "android.widget.ProgressBar")
        UiSemantics.project(loading.getJSONArray("nodes").objects())
        assertEquals(UiSnapshot.signature(tree), UiSnapshot.signature(loading))
        repeat(2) { samples.accept(loading) }
        assertFalse(samples.stableWithoutBaseline())
        assertEquals(Settlement.LOADING, samples.classification())
        samples.accept(tree)
        assertFalse(samples.stableWithoutBaseline())
        samples.accept(tree)
        assertTrue(samples.stableWithoutBaseline())

        val withBaseline = SettledSamples(tree)
        withBaseline.accept(loading)
        withBaseline.accept(tree)
        assertEquals(Settlement.UNCHANGED, withBaseline.classification())
        assertFalse(withBaseline.stableWithoutBaseline())
    }

    private fun tree(text: String): JSONObject {
        val node =
            json(
                "ref" to "e1",
                "sourceId" to "1",
                "windowId" to 1,
                "text" to text,
                "className" to "android.widget.TextView",
                "visible" to true,
                "unavailable" to JSONArray(),
            )
        UiSemantics.project(listOf(node))
        return json(
            "connection" to 1,
            "scope" to json("window" to 1, "package" to "fixture"),
            "partial" to false,
            "nodes" to JSONArray(listOf(node)),
            "windows" to JSONArray(),
        )
    }
}

package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
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

    @Test fun publishedWaitKeepsKnownBaselineAtExpiryBeforeTheFirstSample() {
        val sessions = Sessions({ 0L }, {})
        val lease = sessions.acquire(SessionOwner("pair", "task", "host"))
        val delivery = UiDelivery(sessions, "worker")
        val before = tree("Before").put("sequence", 1).put("selectionScope", JSONObject())
        val capture = delivery.createCapture(lease, UiDelivery.prepareTree(before), false)
        val args = json("screenChange" to true)
        val wait = UiWait(args)
        val run = UiRun("wait", lease, args, UiBudget(5000) { 0 })
        run.remember(wait.start(delivery, lease, before))
        val response = run.response(expired = true)
        val data = response.getJSONObject("data")
        val predicate = data.getJSONObject("predicate")
        assertEquals(Outcome.FAILED, response.getString("result"))
        assertEquals(capture, predicate.getString("baselineCapture"))
        assertEquals(1L, predicate.getLong("baselineSequence"))
        for (field in listOf("sequence", "complete", "currentScope")) assertFalse(predicate.has(field))
        for (field in listOf("requested", "selected", "sent", "dispatchStarted")) assertFalse(data.has(field))
        assertTrue(data.has("snapshotError"))
    }

    @Test fun unsatisfiedWaitKeepsTheLastPredicateAtResponseExpiryWithoutActionFacts() {
        val wait = UiWait(json("gone" to true, "selector" to json("text" to "Saving")))
        val tree = tree("Saving").put("partial", true)
        val run =
            UiRun("wait", Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS), JSONObject(), UiBudget(5000) { 0 })
        run.remember(wait.observe(tree))
        val result = run.response(expired = true)
        val data = result.getJSONObject("data")
        assertEquals(Outcome.FAILED, result.getString("result"))
        assertEquals("gone", data.getJSONObject("predicate").getString("condition"))
        assertFalse(data.getJSONObject("predicate").getBoolean("complete"))
        assertEquals(1, data.getJSONObject("predicate").getInt("matches"))
        assertTrue(data.has("snapshotError"))
        for (field in listOf("selected", "sent", "dispatchStarted")) assertFalse(data.has(field))
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

    @Test fun missingWaitFactDistinguishesInvalidConditionAndDispatch() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        for ((code, expected) in listOf("condition" to "not sent", "timeout" to "failed")) {
            val run = UiRun("wait", lease, JSONObject(), UiBudget(5000) { 0 })
            val error = Rejected(code, "No wait fact")
            assertEquals(expected, run.failure(error).getString("result"))
            run.begin(null)
            assertEquals(Outcome.UNKNOWN, run.failure(error).getString("result"))
        }
    }

    @Test fun productionFailurePreservesKnownFactsAndAddsOnlyUnsentErrors() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        val error = Rejected("snapshotBudget", "Sampling ended")
        for ((command, expected) in listOf("click" to Outcome.NOT_SENT, "wait" to Outcome.FAILED)) {
            val run = UiRun(command, lease, JSONObject(), UiBudget(5000) { 0 })
            val failed = run.failure(error)
            assertEquals(expected, failed.getString("result"))
            assertEquals("snapshotBudget", failed.getJSONObject("error").getString("code"))
        }
        val invalid = UiRun("wait", lease, JSONObject(), UiBudget(5000) { 0 })
        assertEquals(Outcome.NOT_SENT, invalid.failure(Rejected("condition", "Invalid condition")).getString("result"))
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
        for ((command, expected) in listOf("wait" to "failed", "click" to "not sent", "fill" to "not sent")) {
            val run = UiRun(command, lease, JSONObject(), UiBudget(5000) { 0 })
            val response = run.response()
            assertEquals(expected, response.getString("result"))
            if (command == "fill") {
                for (step in listOf("focus", "fill", "submit")) assertFalse(response.getJSONObject("data").has(step))
            }
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
        val unsent = UiRun("check", lease, json("ref" to "e7"), UiBudget(5000) { 0 })
        unsent.resolve("original", original)
        unsent.select("check", original)
        val blocked = unsent.response(expired = true).getJSONObject("data")
        assertFalse(blocked.has("requested"))
        assertEquals("original", blocked.getJSONObject("resolved").getString("method"))
        assertEquals("check", blocked.getJSONObject("selected").getString("action"))
        assertEquals("e7", blocked.getJSONObject("verificationTarget").getString("ref"))
        assertFalse(blocked.getBoolean("dispatchStarted"))
        assertFalse(blocked.has("sent"))

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

    @Test fun rowVerificationKeepsItsOwnControlBeforeSelectionAndAcrossDispatchExpiry() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        val label = node("label", "Option")
        val row = node("row", "Option")
        UiSemantics.project(listOf(label, row))
        for (sent in listOf(false, true)) {
            val run = UiRun("check", lease, json("ref" to "label"), UiBudget(5000) { 0 })
            run.resolve("original", label)
            run.select("check", label)
            val blocked =
                outcome(
                    "check",
                    Outcome.NOT_SENT,
                    "Suggested row has not been dispatched",
                    json(
                        "verificationTarget" to json("ref" to "control"),
                    ),
                )
            run.remember(blocked)
            assertEquals(
                "control",
                run
                    .failure(Rejected("snapshotBudget", "Sampling ended"))
                    .getJSONObject("data")
                    .getJSONObject("verificationTarget")
                    .getString("ref"),
            )
            run.select("click", row)
            if (sent) run.begin(copy(blocked).put("result", Outcome.UNKNOWN).put("reason", "Suggested row click pending"))
            val response = run.response(expired = true)
            assertEquals(if (sent) Outcome.UNKNOWN else Outcome.NOT_SENT, response.getString("result"))
            val data = response.getJSONObject("data")
            assertEquals("label", data.getJSONObject("resolved").getJSONObject("target").getString("ref"))
            assertEquals("row", data.getJSONObject("selected").getJSONObject("target").getString("ref"))
            assertEquals("click", data.getJSONObject("selected").getString("action"))
            assertEquals("control", data.getJSONObject("verificationTarget").getString("ref"))
            assertFalse(data.has("sent"))
            assertEquals(sent, data.getBoolean("dispatchStarted"))
        }
    }

    @Test fun globalAndCoordinateActionsKeepNullNodeTargetsAndReadOnlyResultsOmitActionFacts() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        for (command in listOf("home", "tap", "swipe")) {
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
        val read = UiRun("locate", lease, JSONObject(), UiBudget(5000) { 0 })
        read.remember(outcome("locate", data = json("ref" to "e7")))
        assertEquals(
            setOf("ref"),
            read
                .response()
                .getJSONObject("data")
                .keys()
                .asSequence()
                .toSet(),
        )
    }

    @Test fun compoundReplyKeepsBoundedTargetFactsAndDispatchThroughWireSerialization() {
        val lease = Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS)
        for (name in listOf("Search", "名".repeat(21_845), "\"\\\u0001\n".repeat(16_000))) {
            val target = node("e7").put("editable", true).put("hint", name)
            UiSemantics.project(listOf(target))
            val args = json("selector" to json("name" to "n".repeat(40 * 1024)), "value" to "hello", "submit" to true)
            assertTrue(args.toString().toByteArray(Charsets.UTF_8).size < Limits.JSON_UTF8_BYTES)
            val run = UiRun("fill", lease, args, UiBudget(5000) { 0 })
            run.resolve("selector", target)
            run.begin(null)
            val progress = FillProgress(target, submitRequested = true)
            val stepsWithActions = listOf(FillStep.FOCUS to UiAction.FOCUS, FillStep.FILL to UiAction.FILL, FillStep.SUBMIT to UiAction.IME)
            for ((step, action) in stepsWithActions) {
                progress.started(step)
                val data = UiActions.stepData(action, target)
                if (step == FillStep.FILL) data.put("verification", "matches")
                progress.record(step, outcome(step.command, data = data))
            }
            run.remember(progress.result(run.dispatchStarted))
            val bytes = json("result" to run.response()).toString().toByteArray(Charsets.UTF_8)
            assertTrue(bytes.size < Limits.JSON_UTF8_BYTES)
            val response = JSONObject(bytes.toString(Charsets.UTF_8)).getJSONObject("result")
            assertEquals(Outcome.DONE, response.getString("result"))
            val data = response.getJSONObject("data")
            assertTrue(data.getBoolean("dispatchStarted"))
            assertFalse(data.has("requested"))
            assertFalse(data.has("sent"))
            val steps = listOf("focus", "fill", "submit").map { data.getJSONObject(it).getJSONObject("data") }
            for (step in steps) assertTrue(step.getBoolean("sent"))
            val fillData = data.getJSONObject("fill").getJSONObject("data")
            assertEquals("matches", fillData.getString("verification"))
            assertEquals("e7", fillData.getJSONObject("verificationTarget").getString("ref"))
            val targets =
                listOf(data.getJSONObject("resolved").getJSONObject("target")) +
                    steps.map { it.getJSONObject("selected").getJSONObject("target") }
            val expectedName = if (name.length > 256) name.take(256) + "... (${name.length - 256} chars omitted)" else name
            for (facts in targets) {
                assertEquals(setOf("ref", "role", "name", "windowId", "bounds"), facts.keys().asSequence().toSet())
                assertEquals("e7", facts.getString("ref"))
                assertEquals("textbox", facts.getString("role"))
                assertEquals(expectedName, facts.getString("name"))
                assertEquals(1, facts.getInt("windowId"))
                assertEquals("[0,0,100,100]", facts.getJSONArray("bounds").toString())
            }
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

    @Test fun appConditionRequiresOneActiveTaskWindowWithTheMatchingRootAndScope() {
        val condition = UiCondition(json("app" to "fixture"))
        val window = json("id" to 1, "type" to "application", "active" to true, "rootAvailable" to true, "packageName" to "fixture")
        val ime = json("id" to 2, "type" to "inputMethod", "active" to true)
        val tree = json("scope" to json("window" to 1), "finishedMs" to 20, "windows" to JSONArray(listOf(window, ime)), "partial" to true)
        val evaluation = condition.evaluate(tree)
        assertTrue(evaluation.matched)
        val match = evaluation.predicate!!
        assertEquals("app", match.getString("condition"))
        assertEquals("fixture", match.getString("matchedText"))
        assertEquals(1, match.getInt("windowId"))
        assertFalse(match.has("sampleMs"))
        assertFalse(match.has("scope"))
        window.put("rootAvailable", false)
        val missing = condition.evaluate(tree)
        assertFalse(missing.matched)
        assertNull(missing.predicate)
        window.put("rootAvailable", true).put("packageName", "other")
        assertFalse(condition.evaluate(tree).matched)
        window.put("packageName", "fixture")
        tree.getJSONObject("scope").put("window", 2)
        assertFalse(condition.evaluate(tree).matched)
        tree.getJSONObject("scope").put("window", 1)
        tree.getJSONArray("windows").put(copy(window).put("id", 3))
        assertFalse(condition.evaluate(tree).matched)
        assertEquals("condition", assertThrows(Rejected::class.java) { UiCondition(JSONObject()) }.code)
        assertEquals("condition", assertThrows(Rejected::class.java) { UiCondition(json("app" to "fixture", "text" to "value")) }.code)
        assertEquals("condition", assertThrows(Rejected::class.java) { UiCondition(json("app" to "")) }.code)
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

package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class FillProgressTest {
    @Test fun preselectionFailureKeepsOnlyKnownUnsentFactsAndTheRunsResolvedTarget() {
        for (submit in listOf(false, true)) {
            val progress = FillProgress(null, submitRequested = submit)
            progress.fail(FillStep.FOCUS, Rejected("scopeMismatch", "Ref belongs to a different window"))
            val run = run()
            run.resolve("original", target())
            run.remember(progress.result(run.dispatchStarted))
            val result = run.response()
            assertEquals(Outcome.NOT_SENT, result.getString("result"))
            assertEquals("scopeMismatch", result.getJSONObject("error").getString("code"))
            val data = result.getJSONObject("data")
            assertFalse(data.getBoolean("dispatchStarted"))
            assertFalse(data.has("selected"))
            assertEquals("e7", data.getJSONObject("resolved").getJSONObject("target").getString("ref"))
            assertEquals(submit, data.has("submit"))
            for (step in listOfNotNull("focus", "fill", "submit".takeIf { submit })) {
                val value = data.getJSONObject(step)
                assertEquals(Outcome.NOT_SENT, value.getString("result"))
                assertEquals(
                    setOf("sent"),
                    value
                        .getJSONObject("data")
                        .keys()
                        .asSequence()
                        .toSet(),
                )
                assertFalse(value.getJSONObject("data").getBoolean("sent"))
            }
            assertEquals("Fill has not been dispatched", data.getJSONObject("fill").getString("reason"))
        }
    }

    @Test fun pendingCopiesFreshPreparationAndFailuresUseOnlyTheExplicitStepsDispatchFact() {
        val original = target()
        val fresh = node("e7", "Fresh target").put("bounds", JSONArray(listOf(10, 20, 110, 120)))
        UiSemantics.project(listOf(fresh))
        for (step in FillStep.entries) {
            for (started in listOf(false, true)) {
                val progress = FillProgress(original, submitRequested = true)
                val action =
                    when (step) {
                        FillStep.FOCUS -> UiAction.FOCUS
                        FillStep.FILL -> UiAction.FILL
                        FillStep.SUBMIT -> UiAction.IME
                    }
                val prepared = outcome(step.command, Outcome.NOT_SENT, "Prepared without dispatch", UiActions.stepData(action, fresh))
                progress.record(step, prepared)
                val stored = progress.step(step).toString()
                val pendingReason = "${step.command} result pending"
                val pending = progress.pending(step, pendingReason)
                assertEquals(Outcome.UNKNOWN, pending.getString("result"))
                assertFalse(pending.getJSONObject("data").has("dispatchStarted"))
                for (other in FillStep.entries.filter { it != step }) {
                    assertEquals(progress.step(other).toString(), pending.getJSONObject("data").getJSONObject(other.command).toString())
                }
                val pendingStep = pending.getJSONObject("data").getJSONObject(step.command)
                assertEquals(Outcome.UNKNOWN, pendingStep.getString("result"))
                assertEquals(pendingReason, pendingStep.getString("reason"))
                assertTrue(pendingStep.getJSONObject("data").getBoolean("sent"))
                val pendingTarget = pendingStep.getJSONObject("data").getJSONObject("selected").getJSONObject("target")
                assertEquals("Fresh target", pendingTarget.getString("name"))
                assertEquals("[10,20,110,120]", pendingTarget.getJSONArray("bounds").toString())
                pendingTarget.put("name", "Caller changed pending")
                pendingStep.put("reason", "Caller changed pending")
                assertEquals(stored, progress.step(step).toString())
                if (started) progress.started(step)
                progress.fail(step, IllegalStateException("Platform failed"))
                val failed = progress.step(step)
                assertEquals(if (started) Outcome.UNKNOWN else Outcome.NOT_SENT, failed.getString("result"))
                assertEquals(started, failed.getJSONObject("data").getBoolean("sent"))
                assertEquals("workerFailure", failed.getJSONObject("error").getString("code"))
                assertEquals("Platform failed", failed.getString("reason"))
                val failedData = failed.getJSONObject("data")
                assertEquals("Fresh target", failedData.getJSONObject("selected").getJSONObject("target").getString("name"))
                if (step == FillStep.FILL) assertEquals("e7", failedData.getJSONObject("verificationTarget").getString("ref"))
                assertFalse(prepared.getJSONObject("data").has("sent"))
            }
        }
    }

    @Test fun responseBetweenTheGateAndStepRecordingFreezesTheStartedStep() {
        val progress = FillProgress(target(), submitRequested = true)
        val run = run()
        val pending = progress.pending(FillStep.FOCUS, "Focus result pending")
        run.begin(pending)
        val response = run.response()
        val frozen = response.toString()
        val data = response.getJSONObject("data")
        assertTrue(data.getBoolean("dispatchStarted"))
        assertEquals(Outcome.UNKNOWN, data.getJSONObject("focus").getString("result"))
        assertTrue(data.getJSONObject("focus").getJSONObject("data").getBoolean("sent"))
        assertFalse(progress.step(FillStep.FOCUS).getJSONObject("data").getBoolean("sent"))
        progress.started(FillStep.FOCUS)
        progress.record(FillStep.FOCUS, outcome("focus", reason = "Input focus confirmed"))
        assertTrue(progress.step(FillStep.FOCUS).getJSONObject("data").getBoolean("sent"))
        val error = assertThrows(Rejected::class.java) { run.begin(progress.pending(FillStep.FILL, "Fill result pending")) }
        assertEquals("requestEnded", error.code)
        progress.fail(FillStep.FILL, error)
        run.remember(progress.result(run.dispatchStarted))
        assertEquals(frozen, response.toString())
        assertFalse(progress.step(FillStep.FILL).getJSONObject("data").getBoolean("sent"))
        assertEquals(Outcome.FAILED, progress.result(run.dispatchStarted).getString("result"))
    }

    @Test fun recordedStepsKeepTheirDispatchFactsWhenSubmitPreparationFails() {
        val progress = FillProgress(target(), submitRequested = true)
        for (step in listOf(FillStep.FOCUS, FillStep.FILL)) {
            progress.started(step)
            val result = outcome(step.command, data = json("verification" to "matches"))
            progress.record(step, result)
            result.put("result", Outcome.UNKNOWN)
            assertEquals(Outcome.DONE, progress.step(step).getString("result"))
        }
        progress.fail(FillStep.SUBMIT, Rejected("selectionChanged", "Filled target is no longer identifiable; submit was not sent"))
        val result = progress.result(true)
        assertEquals(Outcome.FAILED, result.getString("result"))
        assertEquals("selectionChanged", result.getJSONObject("error").getString("code"))
        val data = result.getJSONObject("data")
        for (step in listOf("focus", "fill")) assertTrue(data.getJSONObject(step).getJSONObject("data").getBoolean("sent"))
        assertFalse(data.getJSONObject("submit").getJSONObject("data").getBoolean("sent"))
        data.getJSONObject("fill").getJSONObject("data").put("sent", false)
        assertTrue(progress.step(FillStep.FILL).getJSONObject("data").getBoolean("sent"))
        progress.fail(FillStep.FILL, Rejected("workerFailure", "Fill result became unavailable"))
        val unknown = progress.result(true)
        assertEquals(Outcome.UNKNOWN, unknown.getString("result"))
        assertEquals("workerFailure", unknown.getJSONObject("error").getString("code"))
    }

    @Test fun blockedSubmitUsesTheWholeRunsDispatchAndPreservesEarlierFillFacts() {
        for (dispatched in listOf(null, FillStep.FOCUS, FillStep.FILL)) {
            val progress = FillProgress(target(), submitRequested = true)
            val run = run()
            if (dispatched != null) {
                run.begin(null)
                progress.started(dispatched)
            }
            progress.record(FillStep.FOCUS, outcome("focus"))
            progress.record(FillStep.FILL, outcome("fill", data = json("verification" to "matches", "actualValue" to "Earlier value")))
            val gate =
                Verification(VerificationState.DIFFERS, json("actualValue" to "Later value"))
                    .json()
                    .put("verificationTarget", json("ref" to "e7"))
            progress.record(FillStep.SUBMIT, outcome("submit", Outcome.NOT_SENT, "Current field did not match", gate))
            run.remember(progress.result(run.dispatchStarted))
            val result = run.response()
            assertEquals(if (dispatched != null) Outcome.FAILED else Outcome.NOT_SENT, result.getString("result"))
            assertFalse(result.has("error"))
            val data = result.getJSONObject("data")
            assertEquals(dispatched != null, data.getBoolean("dispatchStarted"))
            assertEquals(dispatched == FillStep.FOCUS, data.getJSONObject("focus").getJSONObject("data").getBoolean("sent"))
            val fill = data.getJSONObject("fill").getJSONObject("data")
            assertEquals(dispatched == FillStep.FILL, fill.getBoolean("sent"))
            assertEquals("matches", fill.getString("verification"))
            assertEquals("Earlier value", fill.getString("actualValue"))
            val submit = data.getJSONObject("submit")
            assertFalse(submit.has("error"))
            assertFalse(submit.getJSONObject("data").getBoolean("sent"))
            assertEquals("differs", submit.getJSONObject("data").getString("verification"))
            assertEquals("e7", submit.getJSONObject("data").getJSONObject("verificationTarget").getString("ref"))
            assertEquals("Later value", submit.getJSONObject("data").getString("actualValue"))
        }
    }

    private fun target(): JSONObject = node("e7", "Original target").also { UiSemantics.project(listOf(it)) }

    private fun run(): UiRun =
        UiRun(
            "fill",
            Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS),
            JSONObject(),
            UiBudget(5000) { 0 },
        )
}

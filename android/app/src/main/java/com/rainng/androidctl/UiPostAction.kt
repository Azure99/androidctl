package com.rainng.androidctl

import android.os.SystemClock
import org.json.JSONObject

internal class PostActionObservation(
    val facts: JSONObject,
    val verification: Verification?,
) {
    fun appliedTo(result: JSONObject): JSONObject {
        val answer = verification?.let { UiVerification.verifiedResult(result, it) } ?: result
        val data = answer.getJSONObject("data")
        for (field in facts.keys()) data.put(field, facts.get(field))
        return answer
    }
}

internal enum class Settlement(
    val wire: String,
) {
    UNCHANGED("unchanged"),
    CHANGED("changed"),
    LOADING("loading"),
    UNSETTLED("unsettled"),
}

internal class UiPostAction(
    private val ui: UiBackend,
) {
    // Budget admission and completed conditions stop before different work.
    @Suppress("CyclomaticComplexMethod", "LoopWithTooManyJumpStatements")
    fun settle(
        run: UiRun,
        before: JSONObject?,
        verify: (() -> Verification)? = null,
    ): PostActionObservation {
        val start = SystemClock.elapsedRealtime()
        val settleMs = run.args.getLong("settleMs").coerceAtLeast(0)
        val until = if (verify != null) run.budget.deadline else minOf(run.budget.deadline, start + settleMs)
        val samples = SettledSamples(before)
        var verified = Verification()
        var previousWork = 0L
        var stoppedEarly = false
        if (run.absentWindow != null) ui.snapshots.tree?.let(samples::accept)
        while (run.budget.remaining() > 0 && run.absentWindow == null) {
            val started = SystemClock.elapsedRealtime()
            if (started >= until) break
            if (!run.budget.canSample(previousWork, until)) {
                stoppedEarly = true
                break
            }
            verified = postSample(run, before, until, samples, verify)
            if (verified.terminal) {
                // Keep the terminal verification; the new sample only supplies the delivered tree.
                observeAfterRootLoss(run, before, minOf(run.budget.deadline, start + settleMs), samples)
                break
            }
            previousWork = SystemClock.elapsedRealtime() - started
            val finished =
                samples.stableWithoutBaseline() ||
                    samples.classification() == Settlement.CHANGED ||
                    (verify != null && SystemClock.elapsedRealtime() >= start + settleMs)
            val complete = verify == null || verified.state == VerificationState.MATCHES
            if (run.absentWindow != null) break
            if (finished && complete) break
            run.budget.pause(until)
        }
        val finalObservation =
            ui.snapshots.tree?.takeIf {
                before == null || it.optLong("sequence", -1) > before.optLong("sequence", Long.MAX_VALUE)
            }
        val data =
            samples
                .report(finalObservation, completeWindow = SystemClock.elapsedRealtime() >= until)
                .put("settleMs", SystemClock.elapsedRealtime() - start)
        if (stoppedEarly) data.put("samplingStopped", "Estimated next sample exceeds the observation deadline")
        run.absentWindow?.let { window ->
            data
                .put("settle", "changed")
                .put("absentWindow", window)
                .put(
                    "notice",
                    if (ui.snapshots.tree == null) {
                        "Window $window is absent from the default display inventory; default active observation unavailable"
                    } else {
                        "Window $window is absent from the default display inventory; following observation uses the default active scope"
                    },
                )
        }
        return PostActionObservation(data, verified.takeIf { verify != null })
    }

    private fun observeAfterRootLoss(
        run: UiRun,
        before: JSONObject?,
        until: Long,
        samples: SettledSamples,
    ) {
        val scope = run.args.optJSONObject("scope")
        if (scope != null && scope.length() != 0) return
        val rootUnavailable =
            ui.snapshots.tree
                ?.getJSONArray("missing")
                ?.objects()
                ?.any { it.text("reason") == "rootUnavailable" } == true
        if (!rootUnavailable || SystemClock.elapsedRealtime() >= until) return
        // Do not reuse the failed root-read duration to admit this one observation attempt.
        postSample(run, before, until, samples, verify = null)
    }

    private fun postSample(
        run: UiRun,
        before: JSONObject?,
        until: Long,
        samples: SettledSamples,
        verify: (() -> Verification)?,
    ): Verification =
        try {
            samples.accept(ui.postCapture(run, before, until))
            if (verify != null && run.absentWindow == null) {
                verify()
            } else {
                Verification()
            }
        } catch (error: Exception) {
            samples.missing(message(error))
            Verification()
        }
}

internal class SettledSamples(
    private val before: JSONObject?,
) {
    private val baseline = before?.let(UiSnapshot::signature)
    private var previous: String? = null
    private var same = 0
    private var changed = false
    private var last: JSONObject? = null
    private var error: String? = null

    fun accept(tree: JSONObject) {
        last = tree
        error = null
        val signature = UiSnapshot.signature(tree)
        val complete = !tree.getBoolean("partial") && (before != null || !UiSemantics.loading(tree.getJSONArray("nodes").objects()))
        same =
            if (!complete) {
                0
            } else if (signature == previous) {
                same + 1
            } else {
                1
            }
        previous = if (complete) signature else null
        if (complete && baseline != null && signature != baseline) changed = true
    }

    fun missing(reason: String) {
        previous = null
        same = 0
        error = reason
    }

    fun classification(): Settlement {
        val tree = last ?: return Settlement.UNSETTLED
        if (error != null || tree.getBoolean("partial")) return Settlement.UNSETTLED
        return when {
            UiSemantics.loading(tree.getJSONArray("nodes").objects()) -> Settlement.LOADING
            baseline == null || before?.optBoolean("partial") == true || same < 2 -> Settlement.UNSETTLED
            previous != baseline -> Settlement.CHANGED
            !changed -> Settlement.UNCHANGED
            else -> Settlement.UNSETTLED
        }
    }

    fun stableWithoutBaseline(): Boolean = before == null && same >= 2

    fun report(
        finalObservation: JSONObject?,
        completeWindow: Boolean = true,
    ): JSONObject {
        val settlement = classification().takeUnless { !completeWindow && it == Settlement.UNCHANGED } ?: Settlement.UNSETTLED
        val data = json("settle" to settlement.wire, "observedChange" to ObservedChange.compare(before, finalObservation))
        if (error != null || finalObservation == null) {
            data.put("snapshotError", error ?: "No post-action snapshot within the budget")
        }
        return data
    }
}

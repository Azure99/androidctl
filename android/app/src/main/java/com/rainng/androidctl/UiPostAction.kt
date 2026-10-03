package com.rainng.androidctl

import android.os.SystemClock
import org.json.JSONObject

internal class PostActionObservation(
    val facts: JSONObject,
) {
    fun appliedTo(result: JSONObject): JSONObject {
        val answer = result
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
    ): PostActionObservation {
        val start = SystemClock.elapsedRealtime()
        val settleMs = run.args.getLong("settleMs").coerceAtLeast(0)
        val until = minOf(run.budget.deadline, start + settleMs)
        val samples = SettledSamples(before)
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
            postSample(run, before, until, samples)
            previousWork = SystemClock.elapsedRealtime() - started
            val finished =
                samples.stableWithoutBaseline() ||
                    samples.classification() == Settlement.CHANGED
            if (run.absentWindow != null) break
            if (finished) break
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
        return PostActionObservation(data)
    }

    private fun postSample(
        run: UiRun,
        before: JSONObject?,
        until: Long,
        samples: SettledSamples,
    ) {
        try {
            samples.accept(ui.postCapture(run, before, until))
        } catch (error: Exception) {
            samples.missing(message(error))
        }
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

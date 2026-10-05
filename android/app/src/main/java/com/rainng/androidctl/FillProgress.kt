package com.rainng.androidctl

import org.json.JSONObject

internal enum class FillStep(
    val command: String,
) {
    FOCUS("focus"),
    FILL("fill"),
    SUBMIT("submit"),
}

internal class FillProgress(
    original: JSONObject?,
    submitRequested: Boolean,
) {
    private var focus =
        outcome(
            "focus",
            Outcome.NOT_SENT,
            "Focus has not been dispatched",
            (original?.let { UiActions.stepData(UiAction.FOCUS, copy(it)) } ?: JSONObject()).put("sent", false),
        )
    private var fill = outcome("fill", Outcome.NOT_SENT, "Fill has not been dispatched", json("sent" to false))
    private var submit =
        outcome("submit", Outcome.NOT_SENT, "Submit has not been dispatched", json("sent" to false)).takeIf { submitRequested }
    val hasSubmit: Boolean get() = submit != null

    fun step(step: FillStep): JSONObject = copy(stored(step))

    fun started(step: FillStep) {
        stored(step).getJSONObject("data").put("sent", true)
    }

    fun record(
        step: FillStep,
        value: JSONObject,
    ) {
        val result = copy(value)
        result.getJSONObject("data").put("sent", stored(step).getJSONObject("data").getBoolean("sent"))
        when (step) {
            FillStep.FOCUS -> focus = result
            FillStep.FILL -> fill = result
            FillStep.SUBMIT -> submit = result
        }
    }

    fun fail(
        step: FillStep,
        error: Exception,
    ) {
        val data = copy(stored(step).getJSONObject("data"))
        record(step, outcome(step.command, Outcome.undelivered(data.getBoolean("sent")), message(error), data).put("error", problem(error)))
    }

    fun pending(
        step: FillStep,
        reason: String,
    ): JSONObject {
        val pending = copy(stored(step)).put("result", Outcome.UNKNOWN).put("reason", reason)
        pending.getJSONObject("data").put("sent", true)
        return compoundResult(
            if (step == FillStep.FOCUS) pending else focus,
            if (step == FillStep.FILL) pending else fill,
            if (step == FillStep.SUBMIT) pending else submit,
            Outcome.UNKNOWN,
        )
    }

    fun result(dispatchStarted: Boolean): JSONObject {
        val states = listOfNotNull(focus, fill, submit).map { it.getString("result") }
        val state =
            when {
                Outcome.UNKNOWN in states -> Outcome.UNKNOWN
                states.all { it == Outcome.DONE } -> Outcome.DONE
                !dispatchStarted && Outcome.NOT_SENT in states -> Outcome.NOT_SENT
                else -> Outcome.FAILED
            }
        return compoundResult(focus, fill, submit, state)
    }

    private fun stored(step: FillStep): JSONObject =
        when (step) {
            FillStep.FOCUS -> focus
            FillStep.FILL -> fill
            FillStep.SUBMIT -> requireNotNull(submit)
        }

    private fun compoundResult(
        focus: JSONObject,
        fill: JSONObject,
        submit: JSONObject?,
        state: String,
    ): JSONObject {
        val steps = listOfNotNull(focus, fill, submit)
        val data = json("focus" to copy(focus), "fill" to copy(fill))
        if (submit != null) data.put("submit", copy(submit))
        val answer =
            outcome(
                "fill",
                state,
                steps.joinToString("; ") { "${it.getString("command")}=${it.getString("result")}" },
                data,
            )
        steps.firstOrNull { it.getString("result") != Outcome.DONE }?.optJSONObject("error")?.let { answer.put("error", copy(it)) }
        return answer
    }
}

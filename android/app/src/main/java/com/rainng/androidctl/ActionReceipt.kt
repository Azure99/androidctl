package com.rainng.androidctl

import org.json.JSONObject

internal sealed interface ActionAcceptance {
    data class Accepted(
        val value: Boolean,
    ) : ActionAcceptance

    data class Failed(
        val error: JSONObject,
    ) : ActionAcceptance
}

internal data class ActionReceipt(
    val acceptance: ActionAcceptance,
    val callback: Boolean?,
) {
    fun classify(): String =
        when {
            acceptance is ActionAcceptance.Failed -> Outcome.UNKNOWN
            acceptance is ActionAcceptance.Accepted && !acceptance.value -> if (callback == true) Outcome.UNKNOWN else Outcome.FAILED
            callback == null -> Outcome.UNKNOWN
            callback -> Outcome.DONE
            else -> Outcome.FAILED
        }

    fun json(): JSONObject =
        when (acceptance) {
            is ActionAcceptance.Accepted -> json("accepted" to acceptance.value)
            is ActionAcceptance.Failed -> json("acceptedError" to acceptance.error)
        }.put("callback", callback ?: JSONObject.NULL)
}

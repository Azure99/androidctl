package com.rainng.androidctl

import org.json.JSONObject

internal enum class VerificationState(
    val wire: String,
) {
    MATCHES("matches"),
    DIFFERS("differs"),
    UNOBSERVABLE("unobservable"),
}

internal class Verification(
    val state: VerificationState = VerificationState.UNOBSERVABLE,
    val facts: JSONObject = JSONObject(),
    val terminal: Boolean = false,
) {
    fun json(): JSONObject = copy(facts).put("verification", state.wire)
}

internal object UiVerification {
    private fun state(
        action: UiAction,
        node: JSONObject,
    ): VerificationState =
        when {
            node.text("checked") !in listOf("false", "true", "mixed") -> VerificationState.UNOBSERVABLE
            node.getString("checked") == if (action == UiAction.CHECK) "true" else "false" -> VerificationState.MATCHES
            else -> VerificationState.DIFFERS
        }

    fun evaluate(
        action: UiAction,
        node: JSONObject,
    ): Verification {
        val verified = state(action, node)
        val facts = JSONObject()
        if (verified == VerificationState.UNOBSERVABLE) {
            facts.put(
                "verificationError",
                "Checked-state value is not false, true, or mixed",
            )
        }
        if (verified != VerificationState.UNOBSERVABLE) {
            facts.put(
                "actualValue",
                node.getString("checked"),
            )
        }
        facts.put("checkedModel", node.opt("checkedModel"))
        if (!node.unavailable("stateDescription")) facts.put("stateDescription", node.opt("stateDescription"))
        return Verification(verified, facts)
    }

    fun verifiedResult(
        result: JSONObject,
        verified: Verification,
    ): JSONObject {
        val answer = copy(result)
        val facts = verified.json()
        val data = answer.getJSONObject("data")
        for (field in listOf("verification", "actualValue", "checkedModel", "stateDescription", "verificationError")) {
            data.remove(field)
            if (facts.has(field)) data.put(field, facts.get(field))
        }
        answer.put(
            "result",
            when (verified.state) {
                VerificationState.MATCHES -> Outcome.DONE
                VerificationState.DIFFERS -> Outcome.FAILED
                VerificationState.UNOBSERVABLE -> Outcome.UNKNOWN
            },
        )
        if (verified.state != VerificationState.MATCHES) {
            answer.put(
                "reason",
                if (verified.state == VerificationState.DIFFERS) {
                    "Requested value did not match within the budget"
                } else {
                    facts.optString("verificationError", "Requested value could not be verified")
                },
            )
        }
        return answer
    }
}

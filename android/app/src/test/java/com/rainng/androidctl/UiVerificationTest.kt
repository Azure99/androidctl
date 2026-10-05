package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Test

class UiVerificationTest {
    @Test fun submitPrefersImeAndOnlyUsesEnterForAKnownSingleLineField() {
        val target = node("e7").put("editable", true).put("focused", true).put("isMultiLine", true)
        UiSemantics.project(listOf(target))
        target.put("actions", JSONArray(listOf("ime")))
        assertEquals("ime", UiActions.submitSelection(target).getString("action"))
        target.put("actions", JSONArray())
        assertThrows(Rejected::class.java) { UiActions.submitSelection(target) }
        target.remove("isMultiLine")
        assertThrows(Rejected::class.java) { UiActions.submitSelection(target) }
        target.put("isMultiLine", false)
        val enter = UiActions.submitSelection(target)
        assertEquals("key", enter.getString("action"))
        assertEquals("enter", enter.getString("key"))
        assertEquals("e7", enter.getJSONObject("target").getString("ref"))
        target.put("focused", false)
        assertThrows(Rejected::class.java) { UiActions.submitSelection(target) }
    }

    @Test fun valueFactsDistinguishFormattingHintsAndPrivateValues() {
        val args = json("value" to "1234")
        val node = json("password" to false, "showingHintText" to false, "text" to "1,234", "unavailable" to JSONArray())
        assertEquals("differs", UiVerification.evaluate(UiAction.FILL, args, node).json().getString("verification"))
        node.put("text", "1234")
        assertEquals("matches", UiVerification.evaluate(UiAction.FILL, args, node).json().getString("verification"))
        node.put("showingHintText", true)
        assertEquals("matches", UiVerification.evaluate(UiAction.FILL, json("value" to ""), node).json().getString("verification"))
        node.put("password", true)
        val private = UiVerification.evaluate(UiAction.FILL, args, node).json()
        assertEquals("unobservable", private.getString("verification"))
        assertFalse(private.has("actualValue"))
        node.put("password", false).getJSONArray("unavailable").put(json("field" to "text", "reason" to "truncated"))
        assertEquals("unobservable", UiVerification.evaluate(UiAction.FILL, args, node).json().getString("verification"))
    }

    @Test fun mixedAndUnknownCheckedFactsRemainDistinct() {
        val node = json("checked" to "mixed", "checkedModel" to "triState", "unavailable" to JSONArray())
        assertEquals("differs", UiVerification.evaluate(UiAction.CHECK, JSONObject(), node).json().getString("verification"))
        assertEquals("differs", UiVerification.evaluate(UiAction.UNCHECK, JSONObject(), node).json().getString("verification"))
        assertEquals("mixed", UiVerification.evaluate(UiAction.CHECK, JSONObject(), node).json().getString("actualValue"))
        node.put("checked", "true")
        assertEquals("matches", UiVerification.evaluate(UiAction.CHECK, JSONObject(), node).json().getString("verification"))
        assertEquals("true", UiVerification.evaluate(UiAction.CHECK, JSONObject(), node).json().getString("actualValue"))
        node.put("checked", "false")
        assertEquals("matches", UiVerification.evaluate(UiAction.UNCHECK, JSONObject(), node).json().getString("verification"))
        node.put("checked", "97")
        val facts = UiVerification.evaluate(UiAction.CHECK, JSONObject(), node).json()
        assertEquals("unobservable", facts.getString("verification"))
        assertFalse(facts.has("actualValue"))
    }

    @Test fun laterUnavailableSampleRemovesEarlierValueWithoutErasingDispatch() {
        val original =
            outcome(
                "fill",
                reason = "Earlier value matched",
                data =
                    json(
                        "sent" to true,
                        "verification" to "matches",
                        "actualValue" to "old",
                        "checkedModel" to "binary",
                        "stateDescription" to "Checked",
                        "selected" to json("action" to "fill"),
                        "verificationTarget" to json("ref" to "e7"),
                    ),
            )
        val before = original.toString()
        val observed =
            PostActionObservation(
                json("settle" to "unsettled"),
                Verification(facts = json("verificationError" to "Target no longer readable")),
            )
        val result = observed.appliedTo(original)
        assertEquals(before, original.toString())
        assertEquals(Outcome.UNKNOWN, result.getString("result"))
        assertEquals("Target no longer readable", result.getString("reason"))
        assertFalse(result.getJSONObject("data").has("actualValue"))
        assertFalse(result.getJSONObject("data").has("checkedModel"))
        assertFalse(result.getJSONObject("data").has("stateDescription"))
        assertEquals("fill", result.getJSONObject("data").getJSONObject("selected").getString("action"))
        assertEquals("e7", result.getJSONObject("data").getJSONObject("verificationTarget").getString("ref"))
        assertEquals(true, result.getJSONObject("data").getBoolean("sent"))
        assertEquals("unsettled", result.getJSONObject("data").getString("settle"))
    }

    @Test fun postObservationWithoutVerificationPreservesPlatformResultAndFacts() {
        val result =
            outcome(
                "click",
                Outcome.UNKNOWN,
                "Platform did not confirm action",
                json("sent" to true, "selected" to json("action" to "click"), "settle" to "unchanged"),
            )
        val observed = PostActionObservation(json("settle" to "changed", "settleMs" to 42), null)
        val answer = observed.appliedTo(result)
        assertEquals("click", answer.getString("command"))
        assertEquals(Outcome.UNKNOWN, answer.getString("result"))
        assertEquals("Platform did not confirm action", answer.getString("reason"))
        val data = answer.getJSONObject("data")
        assertEquals(true, data.getBoolean("sent"))
        assertEquals("click", data.getJSONObject("selected").getString("action"))
        assertEquals("changed", data.getString("settle"))
        assertEquals(42, data.getInt("settleMs"))
    }

    @Test fun postObservationProjectsCurrentValueAndRemovesOldVerificationError() {
        val result =
            outcome(
                "check",
                Outcome.UNKNOWN,
                data =
                    json(
                        "sent" to true,
                        "selected" to json("action" to "click"),
                        "verificationTarget" to json("ref" to "e7"),
                        "verification" to "unobservable",
                        "verificationError" to "Earlier value unavailable",
                    ),
            )
        val observed =
            PostActionObservation(
                json("settle" to "changed"),
                Verification(VerificationState.MATCHES, json("actualValue" to "true", "checkedModel" to "binary")),
            )
        val answer = observed.appliedTo(result)
        assertEquals(Outcome.DONE, answer.getString("result"))
        val data = answer.getJSONObject("data")
        assertEquals("matches", data.getString("verification"))
        assertEquals("true", data.getString("actualValue"))
        assertEquals("binary", data.getString("checkedModel"))
        assertFalse(data.has("verificationError"))
        assertEquals(true, data.getBoolean("sent"))
        assertEquals("click", data.getJSONObject("selected").getString("action"))
        assertEquals("e7", data.getJSONObject("verificationTarget").getString("ref"))
        assertEquals("changed", data.getString("settle"))
    }

    @Test fun checkedMetadataKeepsPlatformModelAndNullableDescription() {
        val node = json("checked" to "true", "checkedModel" to "binary", "stateDescription" to null, "unavailable" to JSONArray())
        val known = UiVerification.evaluate(UiAction.CHECK, JSONObject(), node).json()
        assertEquals("binary", known.get("checkedModel"))
        assertEquals(JSONObject.NULL, known.get("stateDescription"))
        node.getJSONArray("unavailable").put(json("field" to "stateDescription"))
        val unavailable = UiVerification.evaluate(UiAction.CHECK, JSONObject(), node).json()
        assertFalse(unavailable.has("stateDescription"))
        assertEquals("matches", unavailable.getString("verification"))
    }
}

package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Test

class UiVerificationTest {
    @Test fun postObservationWithoutVerificationPreservesPlatformResultAndFacts() {
        val result =
            outcome(
                "click",
                Outcome.UNKNOWN,
                "Platform did not confirm action",
                json("sent" to true, "selected" to json("action" to "click"), "settle" to "unchanged"),
            )
        val observed = PostActionObservation(json("settle" to "changed", "settleMs" to 42))
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
}

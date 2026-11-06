package com.rainng.androidctl

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Test

class UiWindowObservationTest {
    @Test fun changingSelectionPolicyDoesNotChangeTheObservedScreen() {
        val selected = json("id" to 1, "rootAvailable" to true, "packageName" to "app")
        val other = json("id" to 2, "bounds" to JSONArray(listOf(0, 0, 100, 20)))
        val defaultScope =
            json(
                "connection" to 1,
                "scope" to json("window" to 1, "package" to "app"),
                "nodes" to JSONArray(),
                "windows" to JSONArray(listOf(selected, other)),
                "partial" to false,
            )
        val packageScope = copy(defaultScope)
        packageScope
            .getJSONArray("windows")
            .getJSONObject(1)
            .put("rootAvailable", true)
            .put("packageName", "system")
        assertEquals("unchanged", ObservedChange.compare(packageScope, defaultScope).getString("state"))
        val samples = SettledSamples(packageScope)
        samples.accept(defaultScope)
        samples.accept(defaultScope)
        assertEquals(Settlement.UNCHANGED, samples.classification())

        val moved = copy(defaultScope)
        moved.getJSONArray("windows").getJSONObject(1).put("bounds", JSONArray(listOf(0, 0, 100, 30)))
        assertEquals("changed", ObservedChange.compare(defaultScope, moved).getString("state"))
        val lostRoot = copy(defaultScope)
        lostRoot.getJSONArray("windows").getJSONObject(0).put("rootAvailable", false)
        assertEquals("changed", ObservedChange.compare(defaultScope, lostRoot).getString("state"))
    }
}

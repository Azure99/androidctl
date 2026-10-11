package com.rainng.androidctl

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertThrows
import org.junit.Test

class UiRefRepairTest {
    @Test fun savedCandidatePoolsRecoverTheIntendedTargetOrReject() {
        // These are complete later observations, not replays of the original repair moment.
        val pools = javaClass.getResourceAsStream("/ref-repair-pools.json")!!.bufferedReader().use { JSONArray(it.readText()) }
        for (pool in pools.objects()) {
            val expected = pool.getJSONObject("expect")
            val tree = pool.getJSONObject("tree")
            val intended = expected.text("intendedRef")
            if (intended.isNotEmpty()) assertNotNull(tree.getJSONArray("nodes").objects().singleOrNull { it.text("ref") == intended })
            if (expected.getString("result") == "selected") {
                assertEquals(pool.getString("case"), intended, UiRefRepair.select(pool.getJSONObject("source"), tree).getString("ref"))
            } else {
                assertThrows(pool.getString("case"), Rejected::class.java) { UiRefRepair.select(pool.getJSONObject("source"), tree) }
            }
        }
    }

    @Test fun sourceRequiresTheOriginalWorkerLeaseCaptureAndRef() {
        val source = json("ref" to "e7", "sourceContext" to "worker:3", "sourceCapture" to "worker:8")
        UiRefRepair.requireSource(source, "e7", "worker:3")
        for ((ref, context) in listOf("e7" to "other:3", "e7" to "worker:4", "e8" to "worker:3")) {
            assertThrows(Rejected::class.java) { UiRefRepair.requireSource(source, ref, context) }
        }
        source.put("sourceCapture", "other:8")
        assertThrows(Rejected::class.java) { UiRefRepair.requireSource(source, "e7", "worker:3") }
    }
}

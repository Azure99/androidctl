package com.rainng.androidctl

import android.content.Context
import android.content.ContextWrapper
import android.content.pm.PackageManager
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class UiActionsTest {
    private class TestContext : ContextWrapper(null) {
        override fun createPackageContext(
            packageName: String,
            flags: Int,
        ): Context = this

        override fun getPackageManager(): PackageManager? = null
    }

    @Test fun fillScopeRejectionKeepsAllRequestedStepResultsBeforeDispatch() {
        val budget = UiBudget(5000) { 0L }
        val backend = UiBackend(TestContext(), Sessions({ 0L }, {}), "worker")
        val field =
            node("e1")
                .put("packageName", "pkg")
                .put("editable", true)
                .put("className", "android.widget.EditText")
                .put("actions", JSONArray(listOf("focus", "fill")))
        UiSemantics.project(listOf(field))
        val tree = json("sequence" to 1, "partial" to false, "nodes" to JSONArray(listOf(field)))
        backend.snapshots.install(
            SnapshotAssembly(
                tree,
                mapOf("e1" to UiTarget(AccessibilityNodeInfo(), field)),
                TreeCollector(emptyList(), json(), budget),
                changed = false,
            ),
        )
        val run =
            UiRun(
                "fill",
                Lease(1, SessionOwner("pair", "task", "host"), 0, Sessions.DEFAULT_IDLE_MS),
                json("ref" to "e1", "value" to "42", "submit" to true, "scope" to json("package" to "other")),
                budget,
            )
        run.remember(UiActions(backend).act(run))
        val response = run.response()
        assertEquals(Outcome.NOT_SENT, response.getString("result"))
        assertEquals("scopeMismatch", response.getJSONObject("error").getString("code"))
        val data = response.getJSONObject("data")
        assertFalse(data.getBoolean("dispatchStarted"))
        for (step in listOf("focus", "fill", "submit")) {
            val result = data.getJSONObject(step)
            assertEquals(Outcome.NOT_SENT, result.getString("result"))
            assertFalse(result.getJSONObject("data").getBoolean("sent"))
        }
        backend.snapshots.close()
    }
}

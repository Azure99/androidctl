package com.rainng.androidctl.agent.conformance

import com.rainng.androidctl.agent.actions.ActionKind
import com.rainng.androidctl.agent.actions.GestureDirection
import com.rainng.androidctl.agent.actions.GlobalAction
import com.rainng.androidctl.agent.actions.NodeAction
import com.rainng.androidctl.agent.actions.ScrollDirection
import com.rainng.androidctl.agent.actions.TargetKind
import com.rainng.androidctl.agent.bootstrap.AccessibilityBoundExecutionFactory
import com.rainng.androidctl.agent.bootstrap.RpcMethodCatalogFactory
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.rpc.RpcEnvironment
import com.rainng.androidctl.agent.screenshot.ScreenshotTaskRunner
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File

class AndroidRpcRawBoundaryTokenConformanceTest {
    @Test
    fun rpcMethodCatalogHostRawCallableMethodsMatchRawBoundaryFixture() {
        val hostRawCallableMethods = fixtureStrings("hostRawCallableMethods")
        val environment = RpcEnvironment(versionProvider = { "1.0.0" })
        val screenshotTaskRunner = ScreenshotTaskRunner.createDefault()
        val catalog =
            try {
                RpcMethodCatalogFactory(
                    environment = environment,
                    accessibilityBoundExecutionFactory = AccessibilityBoundExecutionFactory(environment),
                    screenshotTaskRunner = screenshotTaskRunner,
                ).create()
            } finally {
                screenshotTaskRunner.shutdown(force = true)
            }

        assertEquals(hostRawCallableMethods.toSet(), catalog.methodNames())
        hostRawCallableMethods.forEach { methodName ->
            assertNotNull(methodName, catalog.find(methodName))
        }
        assertNull(catalog.find("raw.rpc"))
    }

    @Test
    fun actionWireTokensMatchRawBoundaryFixture() {
        assertEquals(fixtureStrings("actionKinds"), ActionKind.capabilityWireNames())
        assertEquals(
            fixtureStrings("targetKinds"),
            enumValues<TargetKind>().map(TargetKind::wireName),
        )
        assertEquals(
            fixtureStrings("nodeActions"),
            enumValues<NodeAction>().map(NodeAction::wireName),
        )
        assertEquals(
            fixtureStrings("globalActions"),
            enumValues<GlobalAction>().map(GlobalAction::wireName),
        )
        assertEquals(
            fixtureStrings("scrollDirections"),
            enumValues<ScrollDirection>().map(ScrollDirection::wireName),
        )
        assertEquals(
            fixtureStrings("gestureDirections"),
            enumValues<GestureDirection>().map(GestureDirection::wireName),
        )
    }

    @Test
    fun androidRpcErrorCodesMatchRawBoundaryFixture() {
        assertEquals(
            fixtureStrings("androidRpcErrorCodes"),
            RpcErrorCode.entries.map(RpcErrorCode::name),
        )
    }

    private fun fixtureStrings(key: String): List<String> {
        val array = fixture.getJSONArray(key)
        return List(array.length()) { index -> array.getString(index) }
    }

    private val fixture: JSONObject by lazy { loadRawBoundaryTokenFixture() }

    private fun loadRawBoundaryTokenFixture(): JSONObject {
        val userDir = System.getProperty("user.dir") ?: "."
        var current: File? = File(userDir).absoluteFile
        while (current != null) {
            // Gradle may run Android unit tests from the repo root, android/, or app/.
            val candidate = File(current, FIXTURE_PATH)
            if (candidate.isFile) {
                return JSONObject(candidate.readText(Charsets.UTF_8))
            }
            current = current.parentFile
        }
        throw AssertionError(
            "could not locate $FIXTURE_PATH from user.dir=${System.getProperty("user.dir")}",
        )
    }

    private companion object {
        const val FIXTURE_PATH = "contracts/tests/fixtures/android_rpc_raw_boundary_tokens.json"
    }
}

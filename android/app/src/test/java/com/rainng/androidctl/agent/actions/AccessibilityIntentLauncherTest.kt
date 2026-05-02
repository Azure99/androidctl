package com.rainng.androidctl.agent.actions

import android.content.ActivityNotFoundException
import android.content.Intent
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.testsupport.assertActionException
import com.rainng.androidctl.agent.testsupport.mockService
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test
import org.mockito.ArgumentMatchers.any
import org.mockito.Mockito.doAnswer
import org.mockito.Mockito.mock

class AccessibilityIntentLauncherTest {
    @Test
    fun launchAppFailsWhenNoLaunchableActivityCanBeResolved() {
        val launcher =
            AccessibilityIntentLauncher(
                service = mockService(),
                defaultActivityNameLookup = { null },
                launchIntentFactory = { _, _ -> mock(Intent::class.java) },
            )

        assertActionException(
            expectedCode = RpcErrorCode.ACTION_FAILED,
            expectedMessage = "no launchable activity found for 'com.android.settings'",
            expectedRetryable = false,
        ) {
            launcher.launchApp("com.android.settings")
        }
    }

    @Test
    fun launchAppNormalizesActivityStartFailures() {
        val service = mockService()
        doAnswer { throw ActivityNotFoundException("missing") }.`when`(service).startActivity(any())
        val launcher =
            AccessibilityIntentLauncher(
                service = service,
                defaultActivityNameLookup = { "com.android.settings.Settings" },
                launchIntentFactory = { _, _ -> mock(Intent::class.java) },
            )

        assertActionException(
            expectedCode = RpcErrorCode.ACTION_FAILED,
            expectedMessage = "failed to launch app 'com.android.settings'",
            expectedRetryable = true,
        ) {
            launcher.launchApp("com.android.settings")
        }
    }

    @Test
    fun openUrlNormalizesActivityStartFailures() {
        val service = mockService()
        doAnswer { throw SecurityException("denied") }.`when`(service).startActivity(any())
        val launcher =
            AccessibilityIntentLauncher(
                service = service,
                openUrlIntentFactory = { _ -> mock(Intent::class.java) },
            )

        assertActionException(
            expectedCode = RpcErrorCode.ACTION_FAILED,
            expectedMessage = "failed to open url 'https://example.com'",
            expectedRetryable = true,
        ) {
            launcher.openUrl("https://example.com")
        }
    }

    @Test
    fun openUrlNormalizesIllegalArgumentActivityStartFailures() {
        val service = mockService()
        doAnswer { throw IllegalArgumentException("bad intent") }.`when`(service).startActivity(any())
        val launcher =
            AccessibilityIntentLauncher(
                service = service,
                openUrlIntentFactory = { _ -> mock(Intent::class.java) },
            )

        assertActionException(
            expectedCode = RpcErrorCode.ACTION_FAILED,
            expectedMessage = "failed to open url 'https://example.com'",
            expectedRetryable = true,
        ) {
            launcher.openUrl("https://example.com")
        }
    }

    @Test
    fun launchAppPassesThroughUnexpectedRuntimeExceptions() {
        val service = mockService()
        val failure = IllegalStateException("unexpected runtime bug")
        doAnswer { throw failure }.`when`(service).startActivity(any())
        val launcher =
            AccessibilityIntentLauncher(
                service = service,
                defaultActivityNameLookup = { "com.android.settings.Settings" },
                launchIntentFactory = { _, _ -> mock(Intent::class.java) },
            )

        try {
            launcher.launchApp("com.android.settings")
        } catch (error: IllegalStateException) {
            assertSame(failure, error)
            return
        }

        throw AssertionError("expected IllegalStateException")
    }

    @Test
    fun openUrlIntentSpecPreservesNonWebUriWithoutPackageTargeting() {
        val spec = openUrlIntentSpec("smsto:10086?body=phase-d")

        assertEquals(Intent.ACTION_VIEW, spec.action)
        assertEquals("smsto:10086?body=phase-d", spec.url)
    }

    @Test
    fun openUrlPassesRawTargetToIntentFactory() {
        var capturedUrl: String? = null
        val launcher =
            AccessibilityIntentLauncher(
                service = mockService(),
                openUrlIntentFactory = { url ->
                    capturedUrl = url
                    mock(Intent::class.java)
                },
            )

        val status = launcher.openUrl("smsto:10086?body=phase-d")

        assertEquals(ActionResultStatus.Done, status)
        assertEquals("smsto:10086?body=phase-d", capturedUrl)
    }

    @Test
    fun launchAppResolvesDefaultActivityName() {
        val launcher =
            AccessibilityIntentLauncher(
                service = mockService(),
                defaultActivityNameLookup = { packageName ->
                    assertEquals("com.android.settings", packageName)
                    "com.android.settings.Settings"
                },
                launchIntentFactory = { packageName, activityName ->
                    assertEquals("com.android.settings", packageName)
                    assertEquals("com.android.settings.Settings", activityName)
                    mock(Intent::class.java)
                },
            )

        val status = launcher.launchApp("com.android.settings")

        assertEquals(ActionResultStatus.Done, status)
    }
}

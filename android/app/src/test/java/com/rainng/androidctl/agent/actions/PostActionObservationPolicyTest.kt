package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.runtime.ForegroundObservation
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PostActionObservationPolicyTest {
    @Test
    fun defaultUsesImmediateObservationForScrollWithoutSemanticWaitMeaning() {
        val request =
            ScrollActionRequest(
                target = ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.5"),
                timeoutMs = 2_000L,
                direction = ScrollDirection.Down,
            )
        val initialObservation =
            ForegroundObservation(
                state = ObservedWindowState(packageName = "com.android.settings"),
            )

        val policy =
            PostActionObservationPolicy.default(
                request = request,
                initialObservation = initialObservation,
                expectedPackageName = "com.example.ignored",
            )

        assertEquals(0L, policy.timeoutMs)
        assertEquals(0L, policy.pollIntervalMs)
        assertFalse(policy.requiresGenerationAdvance)
        assertEquals(PackageRequirement.NONE, policy.packageRequirement)
        assertNull(policy.expectedPackageName)
    }

    @Test
    fun defaultUsesImmediateObservationForWebUrlWhenExpectedPackageIsMissingAndInitialPackageIsNotLauncher() {
        val request =
            OpenUrlActionRequest(
                timeoutMs = 2_000L,
                url = "https://example.com/path",
            )
        val initialObservation =
            ForegroundObservation(
                state = ObservedWindowState(packageName = "com.android.chrome"),
            )

        val policy =
            PostActionObservationPolicy.default(
                request = request,
                initialObservation = initialObservation,
                expectedPackageName = null,
            )

        assertEquals(0L, policy.timeoutMs)
        assertEquals(0L, policy.pollIntervalMs)
        assertFalse(policy.requiresGenerationAdvance)
        assertEquals(PackageRequirement.NONE, policy.packageRequirement)
        assertNull(policy.expectedPackageName)
    }

    @Test
    fun defaultUsesTransitionObservationForWebUrlWhenExpectedPackageIsMissingAndInitialPackageIsLauncherLike() {
        val request =
            OpenUrlActionRequest(
                timeoutMs = 2_000L,
                url = "https://example.com/path",
            )
        val initialObservation =
            ForegroundObservation(
                state = ObservedWindowState(packageName = "com.android.launcher3"),
            )

        val policy =
            PostActionObservationPolicy.default(
                request = request,
                initialObservation = initialObservation,
                expectedPackageName = null,
            )

        assertEquals(500L, policy.timeoutMs)
        assertEquals(OBSERVATION_POLL_INTERVAL_MS, policy.pollIntervalMs)
        assertTrue(policy.requiresGenerationAdvance)
        assertEquals(PackageRequirement.CHANGED_FROM_INITIAL, policy.packageRequirement)
        assertNull(policy.expectedPackageName)
    }

    @Test
    fun defaultUsesTransitionObservationForWebUrlWhenExpectedPackageIsMissingAndInitialPackageIsHomeLauncher() {
        val request = openUrlRequest(url = "https://example.com/path")
        val initialObservation =
            ForegroundObservation(
                state = ObservedWindowState(packageName = "com.miui.home"),
            )

        val policy =
            PostActionObservationPolicy.default(
                request = request,
                initialObservation = initialObservation,
                expectedPackageName = null,
            )

        assertEquals(500L, policy.timeoutMs)
        assertEquals(OBSERVATION_POLL_INTERVAL_MS, policy.pollIntervalMs)
        assertTrue(policy.requiresGenerationAdvance)
        assertEquals(PackageRequirement.CHANGED_FROM_INITIAL, policy.packageRequirement)
        assertNull(policy.expectedPackageName)
    }

    @Test
    fun defaultUsesTransitionObservationWhenOpenUrlTargetLacksWebScheme() {
        val request = openUrlRequest(url = "example.com/path")
        val initialObservation =
            ForegroundObservation(
                state = ObservedWindowState(packageName = "com.example.app"),
            )

        val policy =
            PostActionObservationPolicy.default(
                request = request,
                initialObservation = initialObservation,
                expectedPackageName = null,
            )

        assertEquals(500L, policy.timeoutMs)
        assertEquals(OBSERVATION_POLL_INTERVAL_MS, policy.pollIntervalMs)
        assertTrue(policy.requiresGenerationAdvance)
        assertEquals(PackageRequirement.CHANGED_FROM_INITIAL, policy.packageRequirement)
        assertNull(policy.expectedPackageName)
    }

    @Test
    fun defaultUsesTransitionObservationWhenInitialPackageIsBlankEvenForWebUrlTargets() {
        val request = openUrlRequest(url = "https://example.com/path")
        val initialObservation =
            ForegroundObservation(
                state = ObservedWindowState(packageName = ""),
            )

        val policy =
            PostActionObservationPolicy.default(
                request = request,
                initialObservation = initialObservation,
                expectedPackageName = null,
            )

        assertEquals(500L, policy.timeoutMs)
        assertEquals(OBSERVATION_POLL_INTERVAL_MS, policy.pollIntervalMs)
        assertTrue(policy.requiresGenerationAdvance)
        assertEquals(PackageRequirement.CHANGED_FROM_INITIAL, policy.packageRequirement)
        assertNull(policy.expectedPackageName)
    }

    @Test
    fun defaultUsesTransitionObservationWhenInitialPackageIsNullEvenForWebUrlTargets() {
        val request = openUrlRequest(url = "https://example.com/path")
        val initialObservation =
            ForegroundObservation(
                state = ObservedWindowState(packageName = null),
            )

        val policy =
            PostActionObservationPolicy.default(
                request = request,
                initialObservation = initialObservation,
                expectedPackageName = null,
            )

        assertEquals(500L, policy.timeoutMs)
        assertEquals(OBSERVATION_POLL_INTERVAL_MS, policy.pollIntervalMs)
        assertTrue(policy.requiresGenerationAdvance)
        assertEquals(PackageRequirement.CHANGED_FROM_INITIAL, policy.packageRequirement)
        assertNull(policy.expectedPackageName)
    }

    @Test
    fun defaultUsesTransitionObservationWhenOpenUrlSchemeIsNonWeb() {
        val request = openUrlRequest(url = "ftp://example.com/path")
        val initialObservation =
            ForegroundObservation(
                state = ObservedWindowState(packageName = "com.example.app"),
            )

        val policy =
            PostActionObservationPolicy.default(
                request = request,
                initialObservation = initialObservation,
                expectedPackageName = null,
            )

        assertEquals(500L, policy.timeoutMs)
        assertEquals(OBSERVATION_POLL_INTERVAL_MS, policy.pollIntervalMs)
        assertTrue(policy.requiresGenerationAdvance)
        assertEquals(PackageRequirement.CHANGED_FROM_INITIAL, policy.packageRequirement)
        assertNull(policy.expectedPackageName)
    }

    private fun openUrlRequest(url: String): OpenUrlActionRequest =
        OpenUrlActionRequest(
            timeoutMs = 2_000L,
            url = url,
        )
}

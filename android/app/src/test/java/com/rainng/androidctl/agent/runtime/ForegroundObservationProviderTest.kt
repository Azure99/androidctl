package com.rainng.androidctl.agent.runtime

import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class ForegroundObservationProviderTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
    }

    @Test
    fun observeShortCircuitsWhenNotInteractiveThenUsesResolverFromSharedForegroundPath() {
        var interactive = false
        var windowReadCount = 0
        val provider =
            AccessibilityForegroundObservationProvider(
                foregroundWindowCandidatesProvider = {
                    windowReadCount += 1
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 3,
                            packageName = "com.android.settings",
                            active = true,
                            focused = true,
                        ),
                    )
                },
                foregroundObservationStateAccess =
                    object : ForegroundObservationStateAccess {
                        override fun foregroundHintState(): ForegroundHintState =
                            ForegroundHintState(
                                fallbackPackageName = "com.android.settings",
                                fallbackGeneration = 5L,
                                trustedActivitiesByPackage =
                                    mapOf(
                                        "com.android.settings" to
                                            TrustedActivityHint(
                                                activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                                generation = 5L,
                                            ),
                                    ),
                            )

                        override fun foregroundGeneration(): Long = 5L
                    },
                interactiveProvider = { interactive },
            )

        val nonInteractiveObservation = provider.observe()

        assertFalse(nonInteractiveObservation.interactive)
        assertEquals(5L, nonInteractiveObservation.generation)
        assertNull(nonInteractiveObservation.state.packageName)
        assertNull(nonInteractiveObservation.state.activityName)
        assertEquals(0, windowReadCount)

        interactive = true

        val observation = provider.observe()

        assertEquals(5L, observation.generation)
        assertEquals("com.android.settings", observation.state.packageName)
        assertEquals(
            "com.android.settings.Settings\$WifiSettingsActivity",
            observation.state.activityName,
        )
        assertEquals(1, windowReadCount)
    }

    @Test
    fun observeWarnsWhenInteractiveProviderFailsAndUsesInteractiveFallback() {
        var nowMs = 0L
        val warningMessages = mutableListOf<String>()
        val reporter =
            RateLimitedDiagnosticReporter(
                cooldownMs = 100L,
                clockMs = { nowMs },
                warningLogger = { message, _ -> warningMessages += message },
            )
        var windowReadCount = 0
        val provider =
            AccessibilityForegroundObservationProvider(
                foregroundWindowCandidatesProvider = {
                    windowReadCount += 1
                    emptyList()
                },
                foregroundObservationStateAccess = fixedStateAccess(generation = 7L),
                interactiveProvider = { error("interactive unavailable") },
                diagnosticReporter = reporter,
            )

        val firstObservation = provider.observe()
        val secondObservation = provider.observe()
        nowMs = 100L
        provider.observe()

        assertTrue(firstObservation.interactive)
        assertTrue(secondObservation.interactive)
        assertEquals(7L, firstObservation.generation)
        assertEquals(3, windowReadCount)
        assertEquals(
            listOf(
                "foreground interactive state unavailable; using interactive=true",
                "foreground interactive state unavailable; using interactive=true",
            ),
            warningMessages,
        )
    }

    private fun fixedStateAccess(generation: Long): ForegroundObservationStateAccess =
        object : ForegroundObservationStateAccess {
            override fun foregroundHintState(): ForegroundHintState = ForegroundHintState()

            override fun foregroundGeneration(): Long = generation
        }
}

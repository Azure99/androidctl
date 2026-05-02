package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class ForegroundWindowResolverTest {
    @Test
    fun resolvePrefersApplicationWindowPackageOverObservedSystemUiPackage() {
        val resolved =
            ForegroundWindowResolver.resolve(
                windows =
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_SYSTEM,
                            layer = 5,
                            packageName = "com.android.systemui",
                            active = true,
                            focused = true,
                        ),
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 3,
                            packageName = "com.android.settings",
                            active = true,
                            focused = true,
                        ),
                    ),
                hintState =
                    ForegroundHintState(
                        fallbackPackageName = "com.android.systemui",
                        fallbackGeneration = 1L,
                        trustedActivitiesByPackage =
                            mapOf(
                                "com.android.systemui" to
                                    TrustedActivityHint(
                                        activityName = "com.android.systemui.SomeOverlay",
                                        generation = 1L,
                                    ),
                            ),
                    ),
                generation = 1L,
                interactive = true,
            )

        assertEquals("com.android.settings", resolved.packageName)
        assertNull(resolved.activityName)
    }

    @Test
    fun resolveKeepsTrustedObservedActivityWhenResolvedPackageMatches() {
        val resolved =
            ForegroundWindowResolver.resolve(
                windows =
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 2,
                            packageName = "com.android.settings",
                            active = true,
                            focused = true,
                        ),
                    ),
                hintState =
                    ForegroundHintState(
                        fallbackPackageName = "com.android.settings",
                        fallbackGeneration = 3L,
                        trustedActivitiesByPackage =
                            mapOf(
                                "com.android.settings" to
                                    TrustedActivityHint(
                                        activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                        generation = 3L,
                                    ),
                            ),
                    ),
                generation = 3L,
                interactive = true,
            )

        assertEquals("com.android.settings", resolved.packageName)
        assertEquals("com.android.settings.Settings\$WifiSettingsActivity", resolved.activityName)
    }

    @Test
    fun resolveRejectsCrossPackageActivityShapedHintForResolvedPackage() {
        val resolved =
            ForegroundWindowResolver.resolve(
                windows =
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 2,
                            packageName = "com.android.settings",
                            active = true,
                            focused = true,
                        ),
                    ),
                hintState =
                    ForegroundHintState(
                        fallbackPackageName = "com.android.settings",
                        fallbackGeneration = 3L,
                        trustedActivitiesByPackage =
                            mapOf(
                                "com.android.settings" to
                                    TrustedActivityHint(
                                        activityName = "com.fake.overlay.SomeActivity",
                                        generation = 3L,
                                    ),
                            ),
                    ),
                generation = 3L,
                interactive = true,
            )

        assertEquals("com.android.settings", resolved.packageName)
        assertNull(resolved.activityName)
    }

    @Test
    fun resolveRejectsSharedPrefixForeignPackageHintForResolvedPackage() {
        val resolved =
            ForegroundWindowResolver.resolve(
                windows =
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 2,
                            packageName = "com.android.settings",
                            active = true,
                            focused = true,
                        ),
                    ),
                hintState =
                    ForegroundHintState(
                        fallbackPackageName = "com.android.settings",
                        fallbackGeneration = 3L,
                        trustedActivitiesByPackage =
                            mapOf(
                                "com.android.settings" to
                                    TrustedActivityHint(
                                        activityName = "com.android.settingshelper.SomeActivity",
                                        generation = 3L,
                                    ),
                            ),
                    ),
                generation = 3L,
                interactive = true,
            )

        assertEquals("com.android.settings", resolved.packageName)
        assertNull(resolved.activityName)
    }

    @Test
    fun resolveUsesTrustedActivityForResolvedPackageEvenWhenHintFallbackPackageDiffers() {
        val resolved =
            ForegroundWindowResolver.resolve(
                windows =
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 2,
                            packageName = "com.google.android.settings.intelligence",
                            active = true,
                            focused = true,
                        ),
                    ),
                hintState =
                    ForegroundHintState(
                        fallbackPackageName = "com.google.android.inputmethod.latin",
                        fallbackGeneration = 1L,
                        trustedActivitiesByPackage =
                            mapOf(
                                "com.google.android.settings.intelligence" to
                                    TrustedActivityHint(
                                        activityName =
                                            "com.google.android.settings.intelligence.modules.search.activity.SearchActivity",
                                        generation = 9L,
                                    ),
                            ),
                    ),
                generation = 9L,
                interactive = true,
            )

        assertEquals("com.google.android.settings.intelligence", resolved.packageName)
        assertEquals(
            "com.google.android.settings.intelligence.modules.search.activity.SearchActivity",
            resolved.activityName,
        )
    }

    @Test
    fun resolveFallsBackToApplicationWindowPackageWhenWindowIsPresentButNotActiveOrFocused() {
        val resolved =
            ForegroundWindowResolver.resolve(
                windows =
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 2,
                            packageName = "com.android.settings",
                            active = false,
                            focused = false,
                        ),
                    ),
                hintState =
                    ForegroundHintState(
                        fallbackPackageName = "com.android.settings",
                        fallbackGeneration = 4L,
                    ),
                generation = 4L,
                interactive = true,
            )

        assertEquals("com.android.settings", resolved.packageName)
        assertNull(resolved.activityName)
    }

    @Test
    fun resolveUsesStaleTrustedPackageDuringSystemOnlyTransition() {
        val resolved =
            ForegroundWindowResolver.resolve(
                windows =
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_SYSTEM,
                            layer = 4,
                            packageName = "com.android.systemui",
                            active = true,
                            focused = true,
                        ),
                    ),
                hintState =
                    ForegroundHintState(
                        fallbackPackageName = "com.android.settings",
                        fallbackGeneration = 3L,
                        trustedActivitiesByPackage =
                            mapOf(
                                "com.android.settings" to
                                    TrustedActivityHint(
                                        activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                        generation = 3L,
                                    ),
                            ),
                    ),
                generation = 4L,
                interactive = true,
            )

        assertEquals("com.android.settings", resolved.packageName)
        assertNull(resolved.activityName)
    }

    @Test
    fun resolveIgnoresStaleHintsAfterGenerationChangesWhenApplicationWindowHasUnknownPackage() {
        val resolved =
            ForegroundWindowResolver.resolve(
                windows =
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 2,
                            packageName = null,
                            active = true,
                            focused = false,
                        ),
                    ),
                hintState =
                    ForegroundHintState(
                        fallbackPackageName = "com.android.settings",
                        fallbackGeneration = 3L,
                        trustedActivitiesByPackage =
                            mapOf(
                                "com.android.settings" to
                                    TrustedActivityHint(
                                        activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                        generation = 3L,
                                    ),
                            ),
                    ),
                generation = 4L,
                interactive = true,
            )

        assertNull(resolved.packageName)
        assertNull(resolved.activityName)
    }

    @Test
    fun resolveIgnoresSystemPackagesWhenMixedWithRootlessApplicationWindow() {
        val resolved =
            ForegroundWindowResolver.resolve(
                windows =
                    listOf(
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_SYSTEM,
                            layer = 5,
                            packageName = "com.android.systemui",
                            active = true,
                            focused = true,
                        ),
                        ForegroundWindowCandidate(
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 3,
                            packageName = null,
                            active = true,
                            focused = false,
                        ),
                    ),
                hintState =
                    ForegroundHintState(
                        fallbackPackageName = "com.android.settings",
                        fallbackGeneration = 3L,
                        trustedActivitiesByPackage =
                            mapOf(
                                "com.android.settings" to
                                    TrustedActivityHint(
                                        activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                        generation = 3L,
                                    ),
                            ),
                    ),
                generation = 4L,
                interactive = true,
            )

        assertNull(resolved.packageName)
        assertNull(resolved.activityName)
    }

    @Test
    fun readCandidatesWarnsWhenServiceWindowsFailAndUsesEmptyList() {
        val warningMessages = mutableListOf<String>()
        val reader =
            AccessibilityForegroundStateReader(
                service =
                    mock(AccessibilityService::class.java).also { service ->
                        `when`(service.windows).thenThrow(IllegalStateException("windows unavailable"))
                    },
                diagnosticReporter = testReporter(warningMessages),
            )

        val candidates = reader.readCandidates()

        assertEquals(emptyList<ForegroundWindowCandidate>(), candidates)
        assertEquals(
            listOf("foreground windows unavailable; using empty candidate list"),
            warningMessages,
        )
    }

    @Test
    fun readCandidatesWarnsWhenWindowReadFailsAndDropsOnlyThatWindow() {
        val warningMessages = mutableListOf<String>()
        val failingWindow =
            mock(AccessibilityWindowInfo::class.java).also { window ->
                `when`(window.root).thenThrow(IllegalStateException("root unavailable"))
            }
        val usableRoot = mockRoot("com.android.settings")
        val usableWindow =
            mock(AccessibilityWindowInfo::class.java).also { window ->
                `when`(window.root).thenReturn(usableRoot)
                `when`(window.type).thenReturn(AccessibilityWindowInfo.TYPE_APPLICATION)
                `when`(window.layer).thenReturn(3)
                `when`(window.isActive).thenReturn(true)
                `when`(window.isFocused).thenReturn(false)
            }
        val service =
            mock(AccessibilityService::class.java).also { service ->
                `when`(service.windows).thenReturn(listOf(failingWindow, usableWindow))
            }
        val reader =
            AccessibilityForegroundStateReader(
                service = service,
                diagnosticReporter = testReporter(warningMessages),
            )

        val candidates = reader.readCandidates()

        assertEquals(
            listOf(
                ForegroundWindowCandidate(
                    type = AccessibilityWindowInfo.TYPE_APPLICATION,
                    layer = 3,
                    packageName = "com.android.settings",
                    active = true,
                    focused = false,
                ),
            ),
            candidates,
        )
        assertEquals(
            listOf("foreground window unavailable; dropping window candidate"),
            warningMessages,
        )
    }

    @Test
    fun foregroundWindowCandidateWarnsWhenActiveOrFocusedReadsFailAndUsesFalse() {
        val warningMessages = mutableListOf<String>()
        val window =
            mock(AccessibilityWindowInfo::class.java).also { window ->
                `when`(window.type).thenReturn(AccessibilityWindowInfo.TYPE_APPLICATION)
                `when`(window.layer).thenReturn(9)
                `when`(window.isActive).thenThrow(IllegalStateException("active unavailable"))
                `when`(window.isFocused).thenThrow(IllegalStateException("focused unavailable"))
            }

        val candidate =
            foregroundWindowCandidate(
                window = window,
                packageName = "com.android.settings",
                diagnosticReporter = testReporter(warningMessages),
            )

        assertFalse(candidate.active)
        assertFalse(candidate.focused)
        assertEquals(
            listOf(
                "foreground window active state unavailable; using active=false",
                "foreground window focus state unavailable; using focused=false",
            ),
            warningMessages,
        )
    }

    private fun testReporter(warningMessages: MutableList<String>): RateLimitedDiagnosticReporter =
        RateLimitedDiagnosticReporter(
            cooldownMs = 100L,
            clockMs = { 0L },
            warningLogger = { message, _ -> warningMessages += message },
        )

    private fun mockRoot(packageName: String): AccessibilityNodeInfo =
        mock(AccessibilityNodeInfo::class.java).also { root ->
            `when`(root.packageName).thenReturn(packageName)
        }
}

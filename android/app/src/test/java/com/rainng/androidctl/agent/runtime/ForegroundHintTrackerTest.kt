package com.rainng.androidctl.agent.runtime

import android.view.accessibility.AccessibilityEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class ForegroundHintTrackerTest {
    @Test
    fun isTrustedActivityNameRejectsCrossPackageActivityShapedClassNames() {
        assertFalse(
            ForegroundHintTracker.isTrustedActivityName(
                packageName = "com.android.settings",
                windowClassName = "com.fake.overlay.SomeActivity",
            ),
        )
    }

    @Test
    fun isTrustedActivityNameRejectsSharedPrefixForeignPackageClassNames() {
        assertFalse(
            ForegroundHintTracker.isTrustedActivityName(
                packageName = "com.android.settings",
                windowClassName = "com.android.settingshelper.SomeActivity",
            ),
        )
    }

    @Test
    fun windowStateEventCapturesActivityName() {
        val state =
            ForegroundHintTracker.update(
                current = ForegroundHintState(),
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                packageName = "com.android.settings",
                windowClassName = "com.android.settings.Settings\$WifiSettingsActivity",
                generation = 1L,
            )

        assertEquals("com.android.settings", state.fallbackPackageName)
        assertEquals(1L, state.fallbackGeneration)
        assertEquals(
            "com.android.settings.Settings\$WifiSettingsActivity",
            state.trustedActivityName("com.android.settings", currentGeneration = 1L),
        )
    }

    @Test
    fun viewEventsDoNotOverrideActivityName() {
        val initial =
            ForegroundHintState(
                fallbackPackageName = "com.android.settings",
                fallbackGeneration = 1L,
                trustedActivitiesByPackage =
                    mapOf(
                        "com.android.settings" to
                            TrustedActivityHint(
                                activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                generation = 1L,
                            ),
                    ),
            )

        val state =
            ForegroundHintTracker.update(
                current = initial,
                eventType = AccessibilityEvent.TYPE_VIEW_FOCUSED,
                packageName = "com.android.settings",
                windowClassName = "android.widget.EditText",
                generation = 2L,
            )

        assertEquals(initial, state)
    }

    @Test
    fun trustedPackageChangeRetainsPerPackageActivities() {
        val initial =
            ForegroundHintState(
                fallbackPackageName = "com.android.settings",
                fallbackGeneration = 1L,
                trustedActivitiesByPackage =
                    mapOf(
                        "com.android.settings" to
                            TrustedActivityHint(
                                activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                generation = 1L,
                            ),
                    ),
            )

        val state =
            ForegroundHintTracker.update(
                current = initial,
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                packageName = "com.android.chrome",
                windowClassName = "com.android.chrome.MainActivity",
                generation = 2L,
            )

        assertEquals("com.android.chrome", state.fallbackPackageName)
        assertEquals(2L, state.fallbackGeneration)
        assertEquals("com.android.chrome.MainActivity", state.trustedActivityName("com.android.chrome", 2L))
        assertEquals(
            "com.android.settings.Settings\$WifiSettingsActivity",
            state.trustedActivityName("com.android.settings", 1L),
        )
    }

    @Test
    fun viewEventsDoNotReplaceExistingPackageWithTransientSystemUiPackage() {
        val initial =
            ForegroundHintState(
                fallbackPackageName = "com.android.settings",
                fallbackGeneration = 1L,
                trustedActivitiesByPackage =
                    mapOf(
                        "com.android.settings" to
                            TrustedActivityHint(
                                activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                generation = 1L,
                            ),
                    ),
            )

        val state =
            ForegroundHintTracker.update(
                current = initial,
                eventType = AccessibilityEvent.TYPE_VIEW_FOCUSED,
                packageName = "com.android.systemui",
                windowClassName = "android.widget.FrameLayout",
                generation = 2L,
            )

        assertEquals(initial, state)
    }

    @Test
    fun weakWindowStateClassDoesNotClearTrustedActivityForSamePackage() {
        val initial =
            ForegroundHintState(
                fallbackPackageName = "com.android.settings",
                fallbackGeneration = 1L,
                trustedActivitiesByPackage =
                    mapOf(
                        "com.android.settings" to
                            TrustedActivityHint(
                                activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                generation = 1L,
                            ),
                    ),
            )

        val frameLayoutState =
            ForegroundHintTracker.update(
                current = initial,
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                packageName = "com.android.settings",
                windowClassName = "android.widget.FrameLayout",
                generation = 2L,
            )
        val nullClassState =
            ForegroundHintTracker.update(
                current = initial,
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                packageName = "com.android.settings",
                windowClassName = null,
                generation = 2L,
            )

        assertEquals("com.android.settings", frameLayoutState.fallbackPackageName)
        assertEquals(2L, frameLayoutState.fallbackGeneration)
        assertNull(frameLayoutState.trustedActivityName("com.android.settings", currentGeneration = 2L))
        assertEquals("com.android.settings", nullClassState.fallbackPackageName)
        assertEquals(2L, nullClassState.fallbackGeneration)
        assertNull(nullClassState.trustedActivityName("com.android.settings", currentGeneration = 2L))
    }

    @Test
    fun weakWindowStateClassDoesNotReplaceFallbackPackageForDifferentPackage() {
        val initial =
            ForegroundHintState(
                fallbackPackageName = "com.android.settings",
                fallbackGeneration = 1L,
                trustedActivitiesByPackage =
                    mapOf(
                        "com.android.settings" to
                            TrustedActivityHint(
                                activityName = "com.android.settings.Settings\$WifiSettingsActivity",
                                generation = 1L,
                            ),
                    ),
            )

        val state =
            ForegroundHintTracker.update(
                current = initial,
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                packageName = "com.google.android.inputmethod.latin",
                windowClassName = "android.inputmethodservice.SoftInputWindow",
                generation = 2L,
            )

        assertEquals("com.android.settings", state.fallbackPackageName)
        assertEquals(1L, state.fallbackGeneration)
        assertNull(state.trustedActivityName("com.google.android.inputmethod.latin", currentGeneration = 2L))
        assertEquals(
            "com.android.settings.Settings\$WifiSettingsActivity",
            state.trustedActivityName("com.android.settings", currentGeneration = 1L),
        )
    }
}

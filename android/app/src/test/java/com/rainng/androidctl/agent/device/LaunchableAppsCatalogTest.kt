package com.rainng.androidctl.agent.device

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LaunchableAppsCatalogTest {
    @Test
    fun launchableAppsFiltersBlankPackageName() {
        val apps =
            catalog(
                LaunchableAppEntry(
                    packageName = "",
                    appLabel = "Settings",
                    activityName = "com.example.settings.MainActivity",
                ),
                LaunchableAppEntry(
                    packageName = "com.example.browser",
                    appLabel = "Browser",
                    activityName = "com.example.browser.HomeActivity",
                ),
            ).launchableApps()

        assertEquals(1, apps.size)
        assertEquals("com.example.browser", apps.single().packageName)
    }

    @Test
    fun launchableAppsFiltersBlankActivityName() {
        val apps =
            catalog(
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "Settings",
                    activityName = "",
                ),
                LaunchableAppEntry(
                    packageName = "com.example.browser",
                    appLabel = "Browser",
                    activityName = "com.example.browser.HomeActivity",
                ),
            ).launchableApps()

        assertEquals(1, apps.size)
        assertEquals("com.example.browser.HomeActivity", apps.single().activityName)
    }

    @Test
    fun launchableAppsFallsBackToPackageNameWhenLabelIsBlank() {
        val apps =
            catalog(
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "",
                    activityName = "com.example.settings.MainActivity",
                ),
            ).launchableApps()

        assertEquals(1, apps.size)
        assertEquals("com.example.settings", apps.single().appLabel)
    }

    @Test
    fun launchableAppsDeduplicatesPackagesKeepingFirstValidActivity() {
        val apps =
            catalog(
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "Settings",
                    activityName = "com.example.settings.MainActivity",
                ),
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "Settings 2",
                    activityName = "com.example.settings.SecondaryActivity",
                ),
            ).launchableApps()

        assertEquals(1, apps.size)
        assertEquals("com.example.settings.MainActivity", apps.single().activityName)
    }

    @Test
    fun listPayloadSortsByLowercaseLabelThenPackageName() {
        val payload =
            catalog(
                LaunchableAppEntry(
                    packageName = "com.example.zeta",
                    appLabel = "Alpha",
                    activityName = "com.example.zeta.MainActivity",
                ),
                LaunchableAppEntry(
                    packageName = "com.example.alpha",
                    appLabel = "alpha",
                    activityName = "com.example.alpha.MainActivity",
                ),
                LaunchableAppEntry(
                    packageName = "com.example.browser",
                    appLabel = "Browser",
                    activityName = "com.example.browser.HomeActivity",
                ),
            ).listResponse()

        val apps = payload.apps
        assertEquals(3, apps.size)
        assertEquals("com.example.alpha", apps[0].packageName)
        assertEquals("com.example.zeta", apps[1].packageName)
        assertEquals("com.example.browser", apps[2].packageName)
        assertTrue(apps.all { it.launchable })
    }

    @Test
    fun listResponseReturnsEmptyAppsForEmptySource() {
        val payload = catalog().listResponse()

        assertEquals(emptyList<AppEntryResponse>(), payload.apps)
    }

    @Test
    fun listResponseDeduplicatesPackagesAndMarksReturnedAppsLaunchable() {
        val payload =
            catalog(
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "Settings",
                    activityName = "com.example.settings.MainActivity",
                ),
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "Settings 2",
                    activityName = "com.example.settings.SecondaryActivity",
                ),
                LaunchableAppEntry(
                    packageName = "com.example.browser",
                    appLabel = "Browser",
                    activityName = "com.example.browser.HomeActivity",
                ),
            ).listResponse()

        val apps = payload.apps
        assertEquals(listOf("com.example.browser", "com.example.settings"), apps.map { it.packageName })
        assertEquals("Settings", apps.single { it.packageName == "com.example.settings" }.appLabel)
        assertTrue(apps.all { it.launchable })
    }

    @Test
    fun listResponseFallsBackToPackageNameWhenLabelIsBlank() {
        val payload =
            catalog(
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "",
                    activityName = "com.example.settings.MainActivity",
                ),
            ).listResponse()

        assertEquals(1, payload.apps.size)
        assertEquals("com.example.settings", payload.apps.single().appLabel)
        assertTrue(payload.apps.single().launchable)
    }

    @Test
    fun defaultActivityNameUsesFirstLaunchableActivityForPackage() {
        val catalog =
            catalog(
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "Settings",
                    activityName = "com.example.settings.MainActivity",
                ),
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "Settings",
                    activityName = "com.example.settings.SecondaryActivity",
                ),
            )

        assertEquals(
            "com.example.settings.MainActivity",
            catalog.defaultActivityName("com.example.settings"),
        )
    }

    @Test
    fun defaultActivityNameReturnsNullForUnknownPackage() {
        assertNull(
            catalog(
                LaunchableAppEntry(
                    packageName = "com.example.settings",
                    appLabel = "Settings",
                    activityName = "com.example.settings.MainActivity",
                ),
            ).defaultActivityName("com.unknown.app"),
        )
    }

    private fun catalog(vararg entries: LaunchableAppEntry): LaunchableAppsCatalog = LaunchableAppsCatalog { entries.toList() }
}

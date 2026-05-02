package com.rainng.androidctl.agent.device

import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.content.pm.ResolveInfo
import android.os.Build
import org.junit.Assert.assertEquals
import org.junit.Test
import org.mockito.ArgumentMatchers.any
import org.mockito.ArgumentMatchers.anyInt
import org.mockito.ArgumentMatchers.eq
import org.mockito.ArgumentMatchers.same
import org.mockito.Mockito.mock
import org.mockito.Mockito.never
import org.mockito.Mockito.verify
import org.mockito.Mockito.`when`

class PackageManagerLaunchableAppsSourceTest {
    @Test
    fun entriesQueriesActionMainCategoryLauncherIntentByDefault() {
        val packageManager = mock(PackageManager::class.java)
        val launchIntent = mock(Intent::class.java)
        var capturedAction: String? = null
        var capturedCategories: Set<String>? = null
        `when`(
            packageManager.queryIntentActivities(
                any(Intent::class.java),
                anyInt(),
            ),
        ).thenReturn(emptyList())

        val apps =
            PackageManagerLaunchableAppsSource(
                packageManager = packageManager,
                sdkIntProvider = { Build.VERSION_CODES.S_V2 },
                launchIntentFactory = { action, categories ->
                    capturedAction = action
                    capturedCategories = categories
                    launchIntent
                },
            ).entries()

        assertEquals(emptyList<LaunchableAppEntry>(), apps)
        verify(packageManager).queryIntentActivities(same(launchIntent), eq(0))
        assertEquals(Intent.ACTION_MAIN, capturedAction)
        assertEquals(setOf(Intent.CATEGORY_LAUNCHER), capturedCategories)
    }

    @Test
    fun entriesUsesResolveInfoFlagsOnTiramisuAndAbove() {
        val packageManager = mock(PackageManager::class.java)
        val resolveInfo =
            resolveInfo(
                packageName = "com.example.settings",
                activityName = "com.example.settings.MainActivity",
                applicationLabel = "Settings",
                packageManager = packageManager,
            )
        val flags = mock(PackageManager.ResolveInfoFlags::class.java)
        `when`(
            packageManager.queryIntentActivities(
                any(Intent::class.java),
                any(PackageManager.ResolveInfoFlags::class.java),
            ),
        ).thenReturn(listOf(resolveInfo))
        val launchIntent = mock(Intent::class.java)

        val apps =
            PackageManagerLaunchableAppsSource(
                packageManager = packageManager,
                sdkIntProvider = { Build.VERSION_CODES.TIRAMISU },
                launchIntentFactory = { _, _ -> launchIntent },
                resolveInfoFlagsProvider = { flags },
            ).entries()

        assertEquals(1, apps.size)
        assertEquals("com.example.settings", apps.single().packageName)
        verify(packageManager).queryIntentActivities(
            same(launchIntent),
            same(flags),
        )
        verify(packageManager, never()).queryIntentActivities(any(Intent::class.java), anyInt())
    }

    @Test
    fun entriesUsesLegacyQueryBeforeTiramisu() {
        val packageManager = mock(PackageManager::class.java)
        val resolveInfo =
            resolveInfo(
                packageName = "com.example.browser",
                activityName = "com.example.browser.HomeActivity",
                applicationLabel = "Browser",
                packageManager = packageManager,
            )
        `when`(
            packageManager.queryIntentActivities(
                any(Intent::class.java),
                anyInt(),
            ),
        ).thenReturn(listOf(resolveInfo))
        val launchIntent = mock(Intent::class.java)

        val apps =
            PackageManagerLaunchableAppsSource(
                packageManager = packageManager,
                sdkIntProvider = { Build.VERSION_CODES.S_V2 },
                launchIntentFactory = { _, _ -> launchIntent },
            ).entries()

        assertEquals(1, apps.size)
        assertEquals("com.example.browser.HomeActivity", apps.single().activityName)
        verify(packageManager).queryIntentActivities(same(launchIntent), eq(0))
        verify(packageManager, never()).queryIntentActivities(
            any(Intent::class.java),
            any(PackageManager.ResolveInfoFlags::class.java),
        )
    }

    @Test
    fun entriesFallsBackToResolveInfoLabelWhenApplicationLabelIsBlank() {
        val packageManager = mock(PackageManager::class.java)
        val resolveInfo =
            resolveInfo(
                packageName = "com.example.camera",
                activityName = "com.example.camera.CameraActivity",
                applicationLabel = "   ",
                resolveLabel = "Camera",
                packageManager = packageManager,
            )
        `when`(
            packageManager.queryIntentActivities(
                any(Intent::class.java),
                anyInt(),
            ),
        ).thenReturn(listOf(resolveInfo))

        val apps =
            PackageManagerLaunchableAppsSource(
                packageManager = packageManager,
                sdkIntProvider = { Build.VERSION_CODES.S_V2 },
                launchIntentFactory = { _, _ -> mock(Intent::class.java) },
            ).entries()

        assertEquals(1, apps.size)
        assertEquals("Camera", apps.single().appLabel)
    }

    private fun resolveInfo(
        packageName: String,
        activityName: String,
        applicationLabel: String,
        packageManager: PackageManager,
        resolveLabel: String = applicationLabel,
    ): ResolveInfo {
        val applicationInfo = mock(ApplicationInfo::class.java)
        val activityInfo =
            ActivityInfo().apply {
                this.applicationInfo = applicationInfo
                this.packageName = packageName
                this.name = activityName
            }
        `when`(applicationInfo.loadLabel(packageManager)).thenReturn(applicationLabel)
        return object : ResolveInfo() {
            override fun loadLabel(pm: PackageManager): CharSequence = resolveLabel
        }.apply {
            this.activityInfo = activityInfo
        }
    }
}

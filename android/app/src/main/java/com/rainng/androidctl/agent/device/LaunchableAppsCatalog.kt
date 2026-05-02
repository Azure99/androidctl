package com.rainng.androidctl.agent.device

import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build

internal data class LaunchableAppEntry(
    val packageName: String,
    val appLabel: String,
    val activityName: String,
)

internal fun interface LaunchableAppsSource {
    fun entries(): List<LaunchableAppEntry>
}

internal class LaunchableAppsCatalog(
    private val source: LaunchableAppsSource,
) {
    constructor(entriesProvider: () -> List<LaunchableAppEntry>) : this(LaunchableAppsSource(entriesProvider))

    fun listResponse(): AppsListResponse {
        val apps =
            launchableApps()
                .sortedWith(
                    compareBy<LaunchableAppEntry> { it.appLabel.lowercase() }
                        .thenBy { it.packageName },
                ).map { app ->
                    AppEntryResponse(
                        packageName = app.packageName,
                        appLabel = app.appLabel,
                        launchable = true,
                    )
                }

        return AppsListResponse(apps = apps)
    }

    fun defaultActivityName(packageName: String): String? =
        launchableApps()
            .firstOrNull { it.packageName == packageName }
            ?.activityName

    internal fun launchableApps(): List<LaunchableAppEntry> {
        val appsByPackage = linkedMapOf<String, LaunchableAppEntry>()
        source.entries().forEach { entry ->
            val normalizedPackageName = entry.packageName.takeIf(String::isNotBlank) ?: return@forEach
            val normalizedActivityName = entry.activityName.takeIf(String::isNotBlank) ?: return@forEach
            val normalizedLabel = entry.appLabel.takeIf(String::isNotBlank) ?: normalizedPackageName
            appsByPackage.putIfAbsent(
                normalizedPackageName,
                LaunchableAppEntry(
                    packageName = normalizedPackageName,
                    appLabel = normalizedLabel,
                    activityName = normalizedActivityName,
                ),
            )
        }
        return appsByPackage.values.toList()
    }

    companion object {
        fun fromPackageManager(packageManager: PackageManager): LaunchableAppsCatalog =
            LaunchableAppsCatalog(PackageManagerLaunchableAppsSource(packageManager))
    }
}

internal class PackageManagerLaunchableAppsSource(
    private val packageManager: PackageManager,
    private val sdkIntProvider: () -> Int = { Build.VERSION.SDK_INT },
    private val launchIntentFactory: (String, Set<String>) -> Intent = { action, categories ->
        Intent(action).apply {
            categories.forEach { category -> addCategory(category) }
        }
    },
    private val resolveInfoFlagsProvider: () -> PackageManager.ResolveInfoFlags = {
        defaultResolveInfoFlags()
    },
) : LaunchableAppsSource {
    override fun entries(): List<LaunchableAppEntry> {
        val launchIntent =
            launchIntentFactory(
                Intent.ACTION_MAIN,
                setOf(Intent.CATEGORY_LAUNCHER),
            )
        val resolvedActivities =
            if (sdkIntProvider() >= Build.VERSION_CODES.TIRAMISU) {
                queryIntentActivitiesTiramisu(launchIntent)
            } else {
                @Suppress("DEPRECATION")
                packageManager.queryIntentActivities(launchIntent, 0)
            }

        return resolvedActivities.mapNotNull { resolveInfo ->
            val activityInfo = resolveInfo.activityInfo
            if (activityInfo == null) {
                return@mapNotNull null
            }
            val packageName = activityInfo.packageName
            if (packageName.isNullOrBlank()) {
                return@mapNotNull null
            }
            val activityName = activityInfo.name
            if (activityName.isNullOrBlank()) {
                return@mapNotNull null
            }
            val appLabel =
                activityInfo.applicationInfo
                    .loadLabel(packageManager)
                    .toString()
                    .takeIf(String::isNotBlank)
                    ?: resolveInfo.loadLabel(packageManager).toString()
            LaunchableAppEntry(
                packageName = packageName,
                appLabel = appLabel,
                activityName = activityName,
            )
        }
    }

    @Suppress("NewApi")
    private fun queryIntentActivitiesTiramisu(launchIntent: Intent) =
        packageManager.queryIntentActivities(
            launchIntent,
            resolveInfoFlagsProvider(),
        )

    companion object {
        @Suppress("NewApi")
        internal fun defaultResolveInfoFlags(): PackageManager.ResolveInfoFlags = PackageManager.ResolveInfoFlags.of(0)
    }
}

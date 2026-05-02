package com.rainng.androidctl.agent.device

import android.content.Context

internal class AppsListProvider(
    private val context: Context,
) {
    fun list(): AppsListResponse = LaunchableAppsCatalog.fromPackageManager(context.packageManager).listResponse()
}

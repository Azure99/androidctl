package com.rainng.androidctl

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject

@SuppressLint("QueryPermissionsNeeded") // Package queries run under Shizuku shell UID 2000, not the App UID.
internal class SystemActions(
    context: Context,
) {
    private val packages = context.createPackageContext("com.android.shell", 0).packageManager

    @Suppress("DEPRECATION")
    fun apps(): JSONObject {
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val entries = packages.queryIntentActivities(intent, 0).map { it.activityInfo.applicationInfo }.distinctBy { it.packageName }
        return outcome(
            "apps",
            data =
                json(
                    "apps" to
                        JSONArray(
                            entries.sortedBy { it.packageName }.map {
                                json("package" to it.packageName, "name" to packages.getApplicationLabel(it).toString())
                            },
                        ),
                ),
        )
    }
}

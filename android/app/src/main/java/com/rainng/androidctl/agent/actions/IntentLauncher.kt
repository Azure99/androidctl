package com.rainng.androidctl.agent.actions

import android.accessibilityservice.AccessibilityService
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Intent
import androidx.core.net.toUri
import com.rainng.androidctl.agent.device.LaunchableAppsCatalog
import com.rainng.androidctl.agent.errors.RpcErrorCode

internal interface IntentLauncher {
    fun launchApp(packageName: String): ActionResultStatus

    fun openUrl(url: String): ActionResultStatus
}

internal data class OpenUrlIntentSpec(
    val action: String,
    val url: String,
)

internal fun openUrlIntentSpec(url: String): OpenUrlIntentSpec =
    OpenUrlIntentSpec(
        action = Intent.ACTION_VIEW,
        url = url,
    )

internal fun normalizeActivityStartFailure(
    error: RuntimeException,
    message: String,
): ActionException? =
    when (error) {
        is ActivityNotFoundException,
        is SecurityException,
        is IllegalArgumentException,
        ->
            ActionException(
                code = RpcErrorCode.ACTION_FAILED,
                message = message,
                retryable = true,
            )

        else -> null
    }

internal fun throwNormalizedActivityStartFailure(
    error: RuntimeException,
    message: String,
): Nothing = throw checkNotNull(normalizeActivityStartFailure(error, message))

private fun buildOpenUrlIntent(url: String): Intent {
    val spec = openUrlIntentSpec(url)
    return Intent(spec.action, spec.url.toUri()).apply {
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
}

internal class AccessibilityIntentLauncher(
    private val service: AccessibilityService,
    private val defaultActivityNameLookup: (String) -> String? = { packageName ->
        LaunchableAppsCatalog.fromPackageManager(service.packageManager).defaultActivityName(packageName)
    },
    private val launchIntentFactory: (String, String) -> Intent = { packageName, activityName ->
        Intent().apply {
            component = ComponentName(packageName, activityName)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
    },
    private val openUrlIntentFactory: (String) -> Intent = ::buildOpenUrlIntent,
) : IntentLauncher {
    override fun launchApp(packageName: String): ActionResultStatus {
        val resolvedActivityName = defaultActivityNameLookup(packageName)
        val launchIntent =
            resolvedActivityName?.let { launchIntentFactory(packageName, it) } ?: throw ActionException(
                code = RpcErrorCode.ACTION_FAILED,
                message = "no launchable activity found for '$packageName'",
                retryable = false,
            )

        try {
            service.startActivity(launchIntent)
        } catch (error: ActivityNotFoundException) {
            throwNormalizedActivityStartFailure(error, "failed to launch app '$packageName'")
        } catch (error: SecurityException) {
            throwNormalizedActivityStartFailure(error, "failed to launch app '$packageName'")
        } catch (error: IllegalArgumentException) {
            throwNormalizedActivityStartFailure(error, "failed to launch app '$packageName'")
        }
        return ActionResultStatus.Done
    }

    override fun openUrl(url: String): ActionResultStatus {
        val intent = openUrlIntentFactory(url)
        try {
            service.startActivity(intent)
        } catch (error: ActivityNotFoundException) {
            throwNormalizedActivityStartFailure(error, "failed to open url '$url'")
        } catch (error: SecurityException) {
            throwNormalizedActivityStartFailure(error, "failed to open url '$url'")
        } catch (error: IllegalArgumentException) {
            throwNormalizedActivityStartFailure(error, "failed to open url '$url'")
        }
        return ActionResultStatus.Done
    }
}

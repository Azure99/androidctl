package com.rainng.androidctl.agent.runtime

import android.view.accessibility.AccessibilityEvent

data class ObservedWindowState(
    val packageName: String? = null,
    val activityName: String? = null,
)

internal data class ForegroundObservation(
    val state: ObservedWindowState = ObservedWindowState(),
    val generation: Long = 0L,
    val interactive: Boolean = true,
)

internal data class TrustedActivityHint(
    val activityName: String,
    val generation: Long,
)

internal data class ForegroundHintState(
    val fallbackPackageName: String? = null,
    val fallbackGeneration: Long? = null,
    val trustedActivitiesByPackage: Map<String, TrustedActivityHint> = emptyMap(),
) {
    fun fallbackPackageName(
        currentGeneration: Long,
        allowStale: Boolean = false,
    ): String? {
        if (!allowStale && fallbackGeneration != currentGeneration) {
            return null
        }
        return fallbackPackageName?.takeIf(String::isNotBlank)
    }

    fun trustedActivityName(
        packageName: String?,
        currentGeneration: Long,
    ): String? {
        val normalizedPackageName = packageName?.takeIf(String::isNotBlank)
        val hint = normalizedPackageName?.let(trustedActivitiesByPackage::get)
        val trustedActivityName =
            hint
                ?.takeIf { it.generation == currentGeneration }
                ?.activityName
                ?.takeIf(String::isNotBlank)
        return trustedActivityName?.takeIf { activityName ->
            ForegroundHintTracker.isTrustedActivityName(normalizedPackageName, activityName)
        }
    }
}

internal object ForegroundHintTracker {
    fun update(
        current: ForegroundHintState,
        eventType: Int,
        packageName: String?,
        windowClassName: String?,
        generation: Long,
    ): ForegroundHintState {
        if (eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            return current
        }

        val normalizedPackageName = packageName?.takeIf(String::isNotBlank)
        val normalizedActivityName =
            normalizedPackageName?.let { candidatePackageName ->
                windowClassName
                    ?.takeIf(String::isNotBlank)
                    ?.takeIf { isTrustedActivityName(candidatePackageName, it) }
            }
        val trustedActivitiesByPackage = current.trustedActivitiesByPackage.toMutableMap()

        if (normalizedPackageName != null && normalizedActivityName != null) {
            trustedActivitiesByPackage[normalizedPackageName] =
                TrustedActivityHint(
                    activityName = normalizedActivityName,
                    generation = generation,
                )
        }

        val nextFallbackPackageName =
            when {
                normalizedPackageName == null -> current.fallbackPackageName
                normalizedActivityName != null -> normalizedPackageName
                normalizedPackageName == current.fallbackPackageName -> normalizedPackageName
                else -> current.fallbackPackageName
            }
        val nextFallbackGeneration =
            when {
                normalizedPackageName == null -> current.fallbackGeneration
                normalizedActivityName != null -> generation
                normalizedPackageName == current.fallbackPackageName -> generation
                else -> current.fallbackGeneration
            }

        return ForegroundHintState(
            fallbackPackageName = nextFallbackPackageName,
            fallbackGeneration = nextFallbackGeneration,
            trustedActivitiesByPackage = trustedActivitiesByPackage.toMap(),
        )
    }

    internal fun isTrustedActivityName(
        packageName: String?,
        windowClassName: String,
    ): Boolean {
        val simpleName = windowClassName.substringAfterLast('.')
        val normalizedPackageName = packageName?.takeIf(String::isNotBlank)
        val matchesPackage =
            normalizedPackageName != null &&
                (windowClassName == normalizedPackageName || windowClassName.startsWith("$normalizedPackageName."))
        val hasExplicitForeignPackage = windowClassName.contains('.') && !matchesPackage

        return !hasExplicitForeignPackage &&
            !UNTRUSTED_CLASS_PREFIXES.any(windowClassName::startsWith) &&
            !UNTRUSTED_SIMPLE_NAME_SUFFIXES.any(simpleName::endsWith) &&
            (matchesPackage || simpleName.endsWith("Activity"))
    }

    private val UNTRUSTED_CLASS_PREFIXES =
        listOf(
            "android.view.",
            "android.widget.",
            "android.webkit.",
            "androidx.compose.",
            "androidx.recyclerview.",
            "com.google.android.material.",
        )

    private val UNTRUSTED_SIMPLE_NAME_SUFFIXES =
        listOf(
            "Layout",
            "View",
            "Text",
            "Button",
            "Image",
            "RecyclerView",
            "EditText",
            "ScrollView",
            "Toolbar",
            "Dialog",
        )
}

package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.runtime.ForegroundObservation

internal enum class PackageRequirement {
    NONE,
    EXPECTED,
    CHANGED_FROM_INITIAL,
}

/**
 * Raw post-action observation sampling policy for the Android/device boundary.
 *
 * This only decides whether the agent returns an immediate sample or briefly polls for updated
 * raw foreground state after a device action. It is not a host-side settle contract and does not
 * imply semantic completion.
 */
internal data class PostActionObservationPolicy(
    val timeoutMs: Long,
    val pollIntervalMs: Long,
    val requiresGenerationAdvance: Boolean,
    val packageRequirement: PackageRequirement,
    val expectedPackageName: String? = null,
) {
    companion object {
        fun default(
            request: ActionRequest,
            initialObservation: ForegroundObservation,
            expectedPackageName: String?,
        ): PostActionObservationPolicy =
            when (request) {
                is LaunchAppActionRequest ->
                    transitionPolicy(
                        request = request,
                        expectedPackageName = expectedPackageName,
                        packageRequirement = PackageRequirement.EXPECTED,
                    )

                is OpenUrlActionRequest ->
                    if (expectedPackageName != null) {
                        transitionPolicy(
                            request = request,
                            expectedPackageName = expectedPackageName,
                            packageRequirement = PackageRequirement.EXPECTED,
                        )
                    } else if (shouldUseImmediateOpenUrlObservation(request, initialObservation)) {
                        immediatePolicy()
                    } else {
                        transitionPolicy(
                            request = request,
                            expectedPackageName = null,
                            packageRequirement = PackageRequirement.CHANGED_FROM_INITIAL,
                        )
                    }

                is TapActionRequest, is LongTapActionRequest, is GlobalActionRequest, is GestureActionRequest ->
                    generationAdvancePolicy()

                is NodeActionRequest ->
                    if (request.action == NodeAction.Submit) {
                        generationAdvancePolicy()
                    } else {
                        immediatePolicy()
                    }

                is TypeActionRequest ->
                    if (request.input.submit) {
                        generationAdvancePolicy()
                    } else {
                        immediatePolicy()
                    }

                is ScrollActionRequest -> immediatePolicy()
            }

        private fun immediatePolicy(): PostActionObservationPolicy =
            PostActionObservationPolicy(
                timeoutMs = 0L,
                pollIntervalMs = 0L,
                requiresGenerationAdvance = false,
                packageRequirement = PackageRequirement.NONE,
            )

        private fun generationAdvancePolicy(): PostActionObservationPolicy =
            PostActionObservationPolicy(
                timeoutMs = 250L,
                pollIntervalMs = OBSERVATION_POLL_INTERVAL_MS,
                requiresGenerationAdvance = true,
                packageRequirement = PackageRequirement.NONE,
            )

        private fun transitionPolicy(
            request: ActionRequest,
            expectedPackageName: String?,
            packageRequirement: PackageRequirement,
        ): PostActionObservationPolicy =
            PostActionObservationPolicy(
                timeoutMs =
                    observationTimeoutMs(
                        request = request,
                    ),
                pollIntervalMs = OBSERVATION_POLL_INTERVAL_MS,
                requiresGenerationAdvance = true,
                packageRequirement = packageRequirement,
                expectedPackageName = expectedPackageName,
            )
    }
}

internal fun expectedPackageName(request: ActionRequest): String? =
    when (request) {
        is LaunchAppActionRequest -> request.packageName
        else -> null
    }?.takeIf(String::isNotBlank)

private fun observationTimeoutMs(request: ActionRequest): Long =
    when (request) {
        is LaunchAppActionRequest -> transitionObservationTimeoutMs(request.timeoutMs)
        is OpenUrlActionRequest -> transitionObservationTimeoutMs(request.timeoutMs)

        else -> 0L
    }

private fun shouldUseImmediateOpenUrlObservation(
    request: OpenUrlActionRequest,
    initialObservation: ForegroundObservation,
): Boolean =
    isWebUrlTarget(request.url) &&
        !shouldWaitForPackageChange(initialObservation.state.packageName)

private fun transitionObservationTimeoutMs(timeoutMs: Long): Long =
    minOf(
        RequestBudgets.MAX_TRANSITION_OBSERVATION_TIMEOUT_MS,
        maxOf(
            RequestBudgets.MIN_TRANSITION_OBSERVATION_TIMEOUT_MS,
            timeoutMs / RequestBudgets.OBSERVATION_TIMEOUT_DIVISOR,
        ),
    )

private fun isWebUrlTarget(url: String): Boolean {
    val scheme = url.substringBefore(':', missingDelimiterValue = "").takeIf(String::isNotBlank)
    if (scheme == null) {
        return false
    }
    return scheme.equals("http", ignoreCase = true) ||
        scheme.equals("https", ignoreCase = true)
}

private fun shouldWaitForPackageChange(packageName: String?): Boolean {
    val normalizedPackageName = packageName?.takeIf(String::isNotBlank)
    if (normalizedPackageName == null) {
        return true
    }
    return normalizedPackageName.endsWith(HOME_PACKAGE_SUFFIX, ignoreCase = true) ||
        LAUNCHER_PACKAGE_HINTS.any { normalizedPackageName.contains(it, ignoreCase = true) }
}

internal const val OBSERVATION_POLL_INTERVAL_MS = 50L
private const val HOME_PACKAGE_SUFFIX = ".home"
private val LAUNCHER_PACKAGE_HINTS = listOf("launcher", "quickstep")

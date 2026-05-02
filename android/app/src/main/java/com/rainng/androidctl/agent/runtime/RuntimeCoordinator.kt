package com.rainng.androidctl.agent.runtime

import android.annotation.SuppressLint
import android.content.Context
import com.rainng.androidctl.agent.AgentConstants

internal interface RuntimeLifecycle {
    fun initialize(context: Context)

    fun initializeWithDeviceToken(
        context: Context,
        token: String,
    )

    fun markServerRunning(
        host: String = AgentConstants.DEFAULT_HOST,
        port: Int = AgentConstants.DEFAULT_PORT,
    )

    fun markServerStopping()

    fun markServerStopped()

    fun reconcileRuntimeState()

    fun recordRequestSummary(summary: String)

    fun recordError(message: String)

    fun regenerateDeviceToken()

    fun replaceDeviceToken(token: String): Unit = throw UnsupportedOperationException("device token replacement is not configured")
}

@SuppressLint("SyntheticAccessor")
@Suppress("TooManyFunctions")
internal class RuntimeCoordinator(
    private val contextStore: RuntimeContextStore,
    private val factsStore: RuntimeFactsStore,
    private val statusStore: RuntimeStatusStore,
    private val deviceTokenCoordinator: DeviceTokenCoordinator,
    private val mutationLock: RuntimeMutationLock,
    private val collaborators: RuntimeCoordinatorCollaborators,
) : RuntimeLifecycle {
    private val authCoordinator: RuntimeAuthCoordinator = collaborators.authCoordinator
    private val probeReconciler: RuntimeProbeReconciler = collaborators.probeReconciler
    private val foregroundObservationManager: ForegroundObservationManager = collaborators.foregroundObservationManager

    var accessibilityServiceEnabledProbe: (Context) -> Boolean
        get() = probeReconciler.accessibilityServiceEnabledProbe
        set(value) {
            probeReconciler.accessibilityServiceEnabledProbe = value
        }

    var warningLogger: (String) -> Unit
        get() = probeReconciler.warningLogger
        set(value) {
            probeReconciler.warningLogger = value
        }

    var serverRunningProbe: (Context) -> Boolean
        get() = probeReconciler.serverRunningProbe
        set(value) {
            probeReconciler.serverRunningProbe = value
        }

    override fun initialize(context: Context) {
        synchronizeMutation {
            contextStore.setApplicationContext(context.applicationContext)
            deviceTokenCoordinator.initialize(context.applicationContext)
            if (!factsStore.current().hasInitializedAuthState()) {
                authCoordinator.loadInitialToken()
            }
            reconcileRuntimeState()
        }
    }

    override fun initializeWithDeviceToken(
        context: Context,
        token: String,
    ) {
        synchronizeMutation {
            contextStore.setApplicationContext(context.applicationContext)
            deviceTokenCoordinator.initialize(context.applicationContext)
            authCoordinator.replaceToken(token)
            refreshRuntimeInputsLocked()
        }
    }

    override fun markServerRunning(
        host: String,
        port: Int,
    ) {
        synchronizeMutation {
            updateFacts(
                transform = { it.copy(serverPhase = ServerPhase.RUNNING) },
                baseState =
                    statusStore.currentState().clearTransitionErrorState().copy(
                        serverHost = host,
                        serverPort = port,
                    ),
            )
        }
    }

    override fun markServerStopping() {
        synchronizeMutation {
            updateFacts(transform = { it.copy(serverPhase = ServerPhase.STOPPING) })
        }
    }

    override fun markServerStopped() {
        synchronizeMutation {
            updateFacts(transform = { it.copy(serverPhase = ServerPhase.STOPPED) })
        }
    }

    override fun reconcileRuntimeState() {
        synchronizeMutation {
            refreshRuntimeInputsLocked()
        }
    }

    internal fun refreshRuntimeInputs(
        accessibilityConnected: Boolean? = null,
        baseState: AgentRuntimeState = statusStore.currentState(),
    ) {
        synchronizeMutation {
            refreshRuntimeInputsLocked(
                accessibilityConnected = accessibilityConnected,
                baseState = baseState,
            )
        }
    }

    private fun refreshRuntimeInputsLocked(
        accessibilityConnected: Boolean? = null,
        baseState: AgentRuntimeState = statusStore.currentState(),
    ) {
        val context = contextStore.applicationContext()
        updateFacts(
            transform = {
                probeReconciler.reconcile(
                    context = context,
                    currentFacts = it,
                    accessibilityAttached = accessibilityConnected ?: it.accessibilityAttached,
                )
            },
            baseState = baseState,
        )
    }

    override fun recordRequestSummary(summary: String) {
        synchronizeMutation {
            statusStore.updateState { it.copy(lastRequestSummary = summary) }
        }
    }

    override fun recordError(message: String) {
        warningLogger(message)
        synchronizeMutation {
            statusStore.updateState { it.copy(lastError = message) }
        }
    }

    override fun regenerateDeviceToken() {
        synchronizeMutation {
            authCoordinator.regenerateToken()
        }
    }

    override fun replaceDeviceToken(token: String) {
        synchronizeMutation {
            authCoordinator.replaceToken(token)
        }
    }

    fun recordObservedWindowState(
        eventType: Int,
        packageName: String?,
        windowClassName: String?,
    ) {
        synchronizeMutation {
            foregroundObservationManager.recordObservedWindowState(eventType, packageName, windowClassName)
        }
    }

    fun resetForegroundObservationState() {
        synchronizeMutation {
            foregroundObservationManager.reset()
        }
    }

    private fun updateFacts(
        transform: (RuntimeFacts) -> RuntimeFacts,
        baseState: AgentRuntimeState = statusStore.currentState(),
    ) {
        val nextFacts = factsStore.update(transform)
        val projectedState = reconciledRuntimeState(baseState = baseState, runtimeFacts = nextFacts)
        statusStore.updateInputs(
            transform = { runtimeInputs(nextFacts) },
            baseState = projectedState,
        )
    }

    private fun <T> synchronizeMutation(action: () -> T): T = mutationLock.synchronize(action)
}

internal data class RuntimeCoordinatorCollaborators(
    val authCoordinator: RuntimeAuthCoordinator,
    val probeReconciler: RuntimeProbeReconciler,
    val foregroundObservationManager: ForegroundObservationManager,
)

private fun RuntimeFacts.hasInitializedAuthState(): Boolean =
    auth.available ||
        auth.blocked ||
        !auth.currentToken.isNullOrBlank()

internal fun AgentRuntimeState.clearTransitionErrorState(): AgentRuntimeState =
    if (authBlockedMessage == null) {
        copy(lastError = null)
    } else {
        this
    }

package com.rainng.androidctl

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.os.SystemClock
import kotlinx.coroutines.flow.MutableStateFlow
import java.io.FileDescriptor
import java.io.PrintWriter
import java.util.concurrent.ExecutionException
import java.util.concurrent.FutureTask
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

class GatewayService : Service() {
    @Volatile private var network: NetworkGateway? = null

    @Volatile private var worker: WorkerConnection? = null

    @Volatile private var identity: DeviceIdentity? = null

    @Volatile private var pairings: Pairings? = null
    private val pendingRevocations = linkedSetOf<String>()
    private val management = NetworkGateway.pool("management", 1)

    @Volatile private var stopping = false

    @Volatile private var error: String? = null

    @Volatile private var stopFailure: String? = null
    private var stopOwner: Thread? = null
    private var pendingDetach: FutureTask<Unit>? = null

    override fun onCreate() {
        super.onCreate()
        showForegroundNotification()
        synchronized(lifecycleGate) {
            if (current != null) {
                stopSelf()
                return
            }
            current = this
        }
        publishStatus()
        enqueueManagementCommand(::initializeManagedResources)
    }

    private fun showForegroundNotification() {
        val notifications = getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL, getString(R.string.gateway_channel), NotificationManager.IMPORTANCE_LOW),
        )
        val intent = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        startForeground(
            NOTIFICATION,
            Notification
                .Builder(this, CHANNEL)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(
                    getString(R.string.gateway_running),
                ).setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentIntent(intent)
                .build(),
        )
    }

    private fun initializeManagedResources() {
        val newIdentity = DeviceIdentity(this)
        val newPairings = Pairings.get(this)
        val newWorker =
            WorkerConnection(
                this,
                { newPairings.list().map { it.id } },
                { enqueueManagement(::replayRevocations) },
                ::publishStatus,
            )
        val accepted =
            synchronized(lifecycleGate) {
                if (stopping) {
                    false
                } else {
                    identity = newIdentity
                    pairings = newPairings
                    worker = newWorker
                    true
                }
            }
        if (!accepted) return
        newWorker.start()
        if (!stopping) startNetwork()
    }

    override fun onStartCommand(
        intent: Intent?,
        flags: Int,
        startId: Int,
    ): Int = START_NOT_STICKY

    override fun onBind(intent: Intent?): IBinder? = null

    fun refreshAndRecover() {
        if (stopping) {
            stopAsync()
            return
        }
        enqueueManagementCommand {
            val failures = mutableListOf<String>()
            try {
                recoverNetwork()
            } catch (failure: Exception) {
                failures.add(message(failure))
            }
            try {
                refreshWorker()
            } catch (failure: Exception) {
                failures.add(message(failure))
            }
            if (failures.isNotEmpty()) throw IllegalStateException(failures.joinToString("; "))
        }
    }

    private fun recoverNetwork() {
        val previous = network
        if (previous != null && previous.state == GatewayState.FAILED) {
            previous.stop()
            network = null
        }
        if (network == null) startNetwork()
    }

    private fun refreshWorker() {
        val connection = worker
        val alive = connection?.isAttachedWorkerAlive() == true
        if (!alive) connection?.bind()
        val hasPending = synchronized(pendingRevocations) { pendingRevocations.isNotEmpty() }
        if (hasPending) {
            replayRevocations()
        } else if (alive) {
            connection.refreshStatus()
        }
    }

    fun pollWorkerStatus() =
        enqueueManagement {
            if (worker?.isAttachedWorkerAlive() == true) worker?.refreshStatus()
        }

    fun openPairingWindow(finished: () -> Unit) =
        enqueueManagementCommand {
            requireNotNull(network).openPairing()
            finished()
        }

    fun forceExecutor(
        workerId: String,
        finished: () -> Unit,
    ) = enqueueManagementCommand {
        requireNotNull(worker).forceExecutor(workerId)
        finished()
    }

    fun revoke(id: String) {
        Pairings.get(this).revoke(id)
        synchronized(pendingRevocations) { pendingRevocations.add(id) }
        publishStatus()
        enqueueManagementCommand(::replayRevocations)
    }

    // Each explicit event attempts every pending UUID once, retaining failures.
    private fun replayRevocations() {
        val pending = synchronized(pendingRevocations) { pendingRevocations.toList() }
        val failures = mutableListOf<String>()
        for (id in pending) {
            try {
                network?.disconnectPairing(id)
                val connection = worker ?: throw Rejected("workerUnavailable", "Worker is unavailable")
                connection.manage { it.revoke(id) }
                synchronized(pendingRevocations) { pendingRevocations.remove(id) }
            } catch (failure: Exception) {
                failures.add("$id: ${message(failure)}")
            }
        }
        if (failures.isNotEmpty()) {
            throw Rejected(
                "revokeUnconfirmed",
                "Credentials revoked; worker confirmation pending: ${failures.joinToString()}",
            )
        }
    }

    fun requestCertificate(received: (String) -> Unit) = enqueueManagement { received(requireNotNull(identity).pem) }

    // Only a failure before renewal may claim the certificate was not renewed.
    fun renewCertificate() =
        enqueueManagementCommand {
            val previous = network
            try {
                previous?.stop()
            } catch (failure: Exception) {
                throw Rejected(
                    "gatewayStopping",
                    "Certificate not renewed: ${message(failure)}",
                )
            }
            network = null
            requireNotNull(identity).renew()
            startNetwork()
        }

    private fun startNetwork() {
        val candidate = NetworkGateway(requireNotNull(identity), requireNotNull(pairings), requireNotNull(worker)) { publishStatus() }
        synchronized(lifecycleGate) {
            network = candidate
            if (stopping) candidate.beginStop() else candidate.start()
        }
    }

    fun releaseSession(
        workerId: String,
        leaseId: Long,
    ) = enqueueManagementCommand {
        requireNotNull(worker).manage { it.release(workerId, leaseId) }
    }

    fun stopExecutor() =
        enqueueManagementCommand {
            requireNotNull(worker).stopExecutor()
        }

    // Successful commands clear management errors; plain enqueueManagement calls retain them.
    private fun enqueueManagementCommand(action: () -> Unit) =
        enqueueManagement {
            action()
            error = null
        }

    // Local management reports platform and persistence failures to the UI.
    private fun enqueueManagement(action: () -> Unit) {
        if (stopping) return
        try {
            management.execute {
                synchronized(lifecycleGate) { if (stopping) return@execute }
                try {
                    action()
                } catch (failure: Exception) {
                    error = message(failure)
                }
                publishStatus()
            }
        } catch (_: java.util.concurrent.RejectedExecutionException) {
            error = "Management is busy"
            publishStatus()
        }
    }

    private fun publishStatus() {
        if (current !== this) return
        val managementError = error
        val gateway = network?.snapshot()
        val executor = worker?.readAttachedStatus()
        val workerError = worker?.readFailure()
        val deviceId = identity?.id
        val revocations = synchronized(pendingRevocations) { pendingRevocations.toList() }
        synchronized(lifecycleGate) {
            if (current !== this) return
            val stopError = stopFailure
            val gatewayStatus =
                if (stopping) {
                    val state = if (stopError == null) GatewayState.STOPPING else GatewayState.FAILED
                    GatewayStatus(state, stopError)
                } else {
                    gateway ?: GatewayStatus(if (managementError != null) GatewayState.FAILED else GatewayState.STARTING)
                }
            status.value =
                ManagementState(
                    gateway = gatewayStatus,
                    executor = executor,
                    error = stopError ?: managementError ?: gatewayStatus.error ?: workerError,
                    deviceId = deviceId,
                    pendingRevocations = revocations,
                )
        }
    }

    // Failure cannot publish a false stopped state.
    override fun onDestroy() {
        if (current === this) {
            beginStopping()
            stopAsync()
        }
        super.onDestroy()
    }

    private fun beginStopping() {
        synchronized(lifecycleGate) {
            stopping = true
            network?.beginStop()
        }
        management.queue.clear()
        management.shutdown()
    }

    // A blocked platform cleanup retains this owner and publishes failure.
    private fun stopAsync() {
        synchronized(lifecycleGate) {
            if (stopOwner?.isAlive == true || current !== this) return
            stopFailure = null
            val deadline = SystemClock.elapsedRealtime() + NetworkGateway.STOP_MS
            stopOwner =
                Thread({
                    try {
                        publishStatus()
                        awaitManagedResources(deadline)
                        detachWorker(deadline)
                        synchronized(lifecycleGate) {
                            current = null
                            status.value = ManagementState()
                        }
                    } catch (failure: Exception) {
                        stopFailure = message(failure)
                        publishStatus()
                    }
                }, "androidctl-gateway-stop").also { it.start() }
        }
    }

    private fun awaitManagedResources(deadline: Long) {
        val remaining = maxOf(0, deadline - SystemClock.elapsedRealtime())
        if (!management.awaitTermination(remaining, TimeUnit.MILLISECONDS)) {
            throw Rejected(
                "gatewayStopping",
                "Stop incomplete: management is still active; use Refresh status after work ends",
            )
        }
        network?.stop(deadline)
    }

    @Suppress("SwallowedException", "UnsafeCallOnNullableType") // FutureTask wraps the original failure as a non-null cause.
    private fun detachWorker(deadline: Long) {
        if (pendingDetach == null) {
            worker?.let { connection ->
                val task = FutureTask { connection.detach() }
                mainExecutor.execute(task)
                pendingDetach = task
            }
        }
        try {
            pendingDetach?.get(maxOf(0, deadline - SystemClock.elapsedRealtime()), TimeUnit.MILLISECONDS)
        } catch (_: TimeoutException) {
            throw Rejected(
                "gatewayStopping",
                "Stop incomplete: worker detach is still active; use Refresh status after work ends",
            )
        } catch (failure: ExecutionException) {
            pendingDetach = null
            throw failure.cause!!
        }
    }

    override fun dump(
        fd: FileDescriptor,
        writer: PrintWriter,
        args: Array<out String>,
    ) {
        writer.println(status.value.toJson(pairings?.list()))
    }

    companion object {
        private val lifecycleGate = Any()

        @Volatile var current: GatewayService? = null
            private set
        internal val status = MutableStateFlow(ManagementState())
        private const val CHANNEL = "androidctl-connection"
        private const val NOTIFICATION = 1
    }
}

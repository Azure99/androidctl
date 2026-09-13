package com.rainng.androidctl

import android.content.ComponentName
import android.content.Context
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.IBinder
import android.os.RemoteException
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import rikka.shizuku.Shizuku
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

// The App binds a Shizuku UserService that executes device operations as shell UID.
internal class WorkerConnection(
    private val context: Context,
    private val allowedPairings: () -> List<String>,
    private val attached: () -> Unit,
    private val changed: () -> Unit,
) {
    @Volatile private var connection: Attached? = null

    @Volatile private var detached = false

    @Volatile private var attempt: Attempt? = null

    @Volatile private var lastFailure: String? = null

    fun isAttachedWorkerAlive(): Boolean = connection?.target?.asBinder()?.pingBinder() == true

    fun readAttachedStatus(): WorkerStatus? {
        val current = connection
        return if (current?.target?.asBinder()?.pingBinder() == true) current.status else null
    }

    fun readFailure(): String? {
        val pending = attempt
        return if (connection == null &&
            pending?.attaching == true &&
            SystemClock.elapsedRealtime() - pending.started > BIND_TIMEOUT_MS
        ) {
            "Worker binding timed out; use Refresh status on the device"
        } else {
            lastFailure
        }
    }

    private val args =
        Shizuku
            .UserServiceArgs(ComponentName(context, WorkerService::class.java))
            .tag("androidctl-executor")
            .processNameSuffix("executor")
            .daemon(true)
            .debuggable(BuildConfig.DEBUG)
            .version(BuildConfig.VERSION_CODE)

    private inner class Attempt(
        val started: Long,
    ) : ServiceConnection {
        @Volatile var attaching = true

        // Platform/Binder attachment failures are reported without replacing a newer attempt.
        override fun onServiceConnected(
            name: ComponentName,
            binder: IBinder,
        ) {
            if (!current()) return
            var accepted = false
            try {
                val candidate = IExecutor.Stub.asInterface(binder)
                val identity = WorkerStatus.fromJson(decodeWorkerReply(candidate.status()))
                if (identity.version != BuildConfig.VERSION_NAME || identity.uid != android.os.Process.SHELL_UID) {
                    throw Rejected("protocolMismatch", "Worker release or identity differs; stop the old worker first")
                }
                val reconciled =
                    WorkerStatus.fromJson(
                        decodeWorkerReply(candidate.reconcile(JSONArray(allowedPairings()).toString())),
                    )
                synchronized(this@WorkerConnection) {
                    if (!current()) return
                    connection = Attached(candidate, reconciled)
                    lastFailure = null
                    attaching = false
                    accepted = true
                }
            } catch (error: Exception) {
                synchronized(this@WorkerConnection) {
                    if (attempt === this) {
                        connection = null
                        attaching = false
                        lastFailure = "Worker attachment rejected: ${message(error)}"
                    }
                }
            }
            if (accepted) attached()
            changed()
        }

        private fun current(): Boolean = !detached && attempt === this

        override fun onServiceDisconnected(name: ComponentName) {
            synchronized(this@WorkerConnection) {
                if (attempt !== this) return
                if (!attaching && connection == null) return
                connection = null
                attaching = false
                lastFailure = "Worker disconnected"
            }
            changed()
        }
    }

    private val received = Shizuku.OnBinderReceivedListener { bind() }
    private val permission =
        Shizuku.OnRequestPermissionResultListener { _, granted ->
            if (granted == PackageManager.PERMISSION_GRANTED) bind()
        }

    fun start() {
        Shizuku.addRequestPermissionResultListener(permission)
        Shizuku.addBinderReceivedListenerSticky(received)
    }

    // Shizuku and Binder failures require explicit user retry.
    fun bind() =
        context.mainExecutor.execute {
            synchronized(this) {
                if (detached || isAttachedWorkerAlive()) return@execute
                val previous = attempt
                if (previous?.attaching == true && SystemClock.elapsedRealtime() - previous.started < BIND_TIMEOUT_MS) {
                    changed()
                    return@execute
                }
                try {
                    requireShellShizuku()
                    if (Shizuku.checkSelfPermission() != PackageManager.PERMISSION_GRANTED) {
                        throw Rejected("shizukuPermission", "Authorize AndroidCtl in Shizuku")
                    }
                    if (previous != null) Shizuku.unbindUserService(args, previous, false)
                    attempt = null
                    connection = null
                    val next = Attempt(SystemClock.elapsedRealtime())
                    attempt = next
                    lastFailure = null
                    Shizuku.bindUserService(args, next)
                } catch (error: Exception) {
                    attempt?.attaching = false
                    lastFailure = message(error)
                }
                changed()
            }
        }

    fun call(request: JSONObject): JSONObject {
        val encoded = request.toString()
        if (encoded.toByteArray().size > Limits.JSON_UTF8_BYTES) throw Rejected("jsonLimit", "Request exceeds the JSON budget")
        return decodeWorkerReply(requireTarget().call(encoded))
    }

    fun manage(action: (IExecutor) -> String) {
        val current = requireTarget()
        val value = WorkerStatus.fromJson(decodeWorkerReply(action(current)))
        synchronized(this) { if (connection?.target === current) connection = Attached(current, value) }
    }

    fun refreshStatus() = manage { it.status() }

    fun stopExecutor() {
        val (current, previous) = synchronized(this) { requireTarget() to attempt }
        decodeWorkerReply(current.prepareStop())
        destroyAndConfirm(current)
        synchronized(this) {
            if (attempt === previous && (connection == null || connection?.target === current)) {
                connection = null
                lastFailure = null
                previous?.attaching = false
            }
        }
    }

    private fun destroyAndConfirm(current: IExecutor) {
        val binder = current.asBinder()
        val died = CountDownLatch(1)
        val observer = IBinder.DeathRecipient { died.countDown() }
        val deadline = SystemClock.elapsedRealtime() + FORCE_MS
        var linked = false
        var dispatchFailure: RemoteException? = null
        try {
            try {
                binder.linkToDeath(observer, 0)
                linked = true
                current.destroy()
            } catch (error: RemoteException) {
                // destroy exits the process before its synchronous reply can arrive.
                dispatchFailure = error
            }
            val confirmed =
                !binder.isBinderAlive ||
                    linked &&
                    died.await((deadline - SystemClock.elapsedRealtime()).coerceAtLeast(0L), TimeUnit.MILLISECONDS)
            if (!confirmed) {
                val detail = dispatchFailure?.let { "; ${message(it)}" }.orEmpty()
                throw Rejected(
                    "workerExitUnconfirmed",
                    "Worker exit is not confirmed; inspect status before another explicit action$detail",
                )
            }
        } finally {
            if (linked) binder.unlinkToDeath(observer, 0)
        }
    }

    @Synchronized fun detach() {
        detached = true
        connection = null
        Shizuku.removeBinderReceivedListener(received)
        Shizuku.removeRequestPermissionResultListener(permission)
        val previous = attempt
        if (previous != null && Shizuku.pingBinder()) Shizuku.unbindUserService(args, previous, false)
        attempt = null
    }

    private fun requireTarget(): IExecutor {
        if (!Shizuku.pingBinder()) throw Rejected("shizukuUnavailable", "Start Shizuku on the device")
        if (Shizuku.checkSelfPermission() != PackageManager.PERMISSION_GRANTED) {
            throw Rejected("shizukuPermission", "Authorize AndroidCtl in Shizuku")
        }
        val current = connection?.target
        if (current?.asBinder()?.pingBinder() != true) {
            throw Rejected("workerUnavailable", readFailure() ?: "Worker is unavailable; check the device App")
        }
        return current
    }

    private data class Attached(
        val target: IExecutor,
        val status: WorkerStatus,
    )

    private companion object {
        const val FORCE_MS = 5000L
    }
}

internal fun requireShellShizuku() {
    if (!Shizuku.pingBinder()) throw Rejected("shizukuUnavailable", "Start Shizuku using ADB or wireless debugging on the device")
    val uid = Shizuku.getUid()
    if (uid != android.os.Process.SHELL_UID) {
        val reason =
            if (uid == 0) {
                "Stop root mode and start Shizuku using ADB or wireless debugging"
            } else {
                "Cannot confirm shell identity (Shizuku UID $uid); check Shizuku and use Refresh status"
            }
        throw Rejected("shizukuMode", reason)
    }
}

internal fun decodeWorkerReply(body: String): JSONObject {
    val response = JSONObject(body)
    val error = response.optJSONObject("error")
    if (error != null) throw Rejected(error.getString("code"), error.getString("message"), response.optBoolean("uncertain"))
    return response.getJSONObject("result")
}

private const val BIND_TIMEOUT_MS = 5000L

package com.rainng.androidctl

import android.content.Context
import android.os.Binder
import android.os.Process
import android.os.SystemClock
import androidx.annotation.Keep
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.system.exitProcess

@Keep
class WorkerService(
    context: Context,
) : IExecutor.Stub() {
    private val appUid = context.applicationInfo.uid
    private val workerLock = WorkerLock()
    private val workerId = token()
    private val sessions = Sessions(SystemClock::elapsedRealtime)
    private val files = FileJobs(sessions)
    private val shell = ShellJobs(context, sessions, workerLock.directory, workerLock::check)
    private val timer = Executors.newSingleThreadScheduledExecutor { Thread(it, "androidctl-idle") }
    private val maintenanceFailures = ConcurrentHashMap<String, String>()

    init {
        maintenance("sessionExpiry", sessions::expire)
        maintenance("fileExpiry", files::expire)
    }

    // Unexpected failure ends only this periodic responsibility and remains visible.
    private fun maintenance(
        name: String,
        action: () -> Unit,
    ) {
        timer.scheduleWithFixedDelay({
            try {
                action()
            } catch (error: Exception) {
                maintenanceFailures[name] = "Periodic maintenance stopped: $error"
                throw error
            }
        }, 1, 1, TimeUnit.SECONDS)
    }

    override fun status(): String = guarded { status(sessions.localStatus()) }

    override fun call(request: String): String =
        guarded {
            val body = JSONObject(request)
            val deadline = body.getLong("deadlineMs")
            if (SystemClock.elapsedRealtime() >= deadline) {
                throw Rejected("timeout", "Request budget exhausted before worker dispatch")
            }
            val method = body.getString("method")
            val owner = SessionOwner(body.getString("pairingId"), body.getString("session"), body.getString("pairingName"))
            sessions.requireAuthorized(owner)
            if (method == "status") {
                outcome(method, data = status(sessions.callerStatus(owner), owner))
            } else {
                if (method !in RpcMethods.ALLOWED_WHILE_STOPPING) accepting()
                when (method) {
                    "file.begin" -> files.start(owner, body.getJSONObject("args"))
                    "file.get", "file.cancel", "file.chunk", "file.finish" -> files.call(owner, method, body.getJSONObject("args"))
                    "shell.start" -> shell.start(owner, body.getJSONObject("args"))
                    "job.get" -> shell.get(owner, body.getJSONObject("args"))
                    "job.cancel" -> shell.cancel(owner, body.getJSONObject("args"))
                    "output.read" -> shell.output(owner, body.getJSONObject("args"))

                    "session.open", "session.close", "session.takeover" ->
                        sessionCommand(
                            method,
                            owner,
                            body.getJSONObject("args"),
                        )
                    else -> throw Rejected("method", "Unsupported worker method")
                }
            }
        }

    private fun sessionCommand(
        method: String,
        owner: SessionOwner,
        args: JSONObject,
    ): JSONObject =
        when (method) {
            "session.open" -> {
                val seconds = args.getLong("idleSeconds")
                if (seconds !in 1L..MAX_IDLE_SECONDS) throw Rejected("idle", "Idle timeout must be 1 to $MAX_IDLE_SECONDS seconds")
                sessions.open(owner, idleMs = TimeUnit.SECONDS.toMillis(seconds))
                outcome("session open", reason = displayName(owner.name))
            }
            "session.close" -> {
                sessions.close(owner)
                outcome("session close")
            }
            else -> {
                val previous = sessions.takeover(owner)
                outcome("session", reason = "took over ${previous?.let(::displayName) ?: "idle device"}")
            }
        }

    override fun release(
        expectedWorkerId: String,
        leaseId: Long,
    ): String =
        managed {
            if (expectedWorkerId != workerId) {
                throw Rejected("sessionChanged", "The displayed worker has changed; refresh first")
            }
            sessions.releaseLocal(leaseId)
        }

    override fun revoke(pairingId: String): String =
        managed {
            sessions.revoke(pairingId)
            shell.revoke(pairingId)
            files.revoke(pairingId)
        }

    override fun reconcile(allowedPairings: String): String =
        managed {
            val values = JSONArray(allowedPairings)
            val allowed = (0 until values.length()).map(values::getString).toSet()
            val retained = sessions.retainedPairings() + shell.retainedPairings() + files.retainedPairings()
            for (pairing in retained - allowed) {
                sessions.revoke(pairing)
                shell.revoke(pairing)
                files.revoke(pairing)
            }
        }

    override fun prepareStop(): String =
        managed {
            shell.prepareStop {
                files.prepareStop {
                    sessions.stop()
                    timer.shutdown()
                }
            }
        }

    private fun managed(action: () -> Unit): String =
        guarded {
            action()
            status(sessions.localStatus())
        }

    override fun destroy() {
        if (Binder.getCallingUid() != appUid && Binder.getCallingUid() != Process.SHELL_UID) throw SecurityException("caller")
        exitProcess(0)
    }

    private fun accepting() {
        if (sessions.stopped) throw Rejected("stopping", "Worker is stopping")
        workerLock.check()
    }

    private fun status(
        session: JSONObject,
        owner: SessionOwner? = null,
    ): JSONObject =
        json(
            "version" to BuildConfig.VERSION_NAME,
            "uid" to Process.myUid(),
            "workerId" to workerId,
            "worker" to if (sessions.stopped) "stopping" else "ready",
            "session" to session,
            "helpers" to shell.snapshot(owner),
            "diagnostics" to json("maintenance" to JSONObject(maintenanceFailures)),
        )

    // Binder returns structured platform failures to the gateway.
    private fun guarded(action: () -> JSONObject): String =
        try {
            if (Binder.getCallingUid() != appUid) throw SecurityException("Only the gateway App can call this worker")
            val value = json("result" to action()).toString()
            if (value.toByteArray().size > Limits.JSON_UTF8_BYTES) {
                throw Rejected("jsonLimit", "Reply exceeds the JSON budget", uncertain = true)
            }
            value
        } catch (error: Exception) {
            json("error" to problem(error), "uncertain" to uncertain(error)).toString()
        }

    private companion object {
        const val MAX_IDLE_SECONDS = 86400L
    }
}

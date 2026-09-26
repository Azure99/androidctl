package com.rainng.androidctl

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

internal class ShellJobs(
    context: Context,
    private val sessions: Sessions,
    directory: File,
    private val checkWorker: () -> Unit,
) {
    private val executable = context.applicationInfo.nativeLibraryDir + "/libandroidctl_exec.so"
    private val slots = HelperSlots(directory)
    private val outputs = OutputStore()
    private val jobs = JobTable<ShellJob>("Job", "Shell helpers are still alive; inspect their jobs before stopping the worker")

    fun start(
        owner: SessionOwner,
        args: JSONObject,
    ): JSONObject {
        val script = args.getString("script")
        val background = args.optBoolean("background")
        val runMs = args.optLong("runMs", if (background) Limits.MAX_RUN_MS else DEFAULT_RUN_MS)
        if (script.toByteArray().size > SCRIPT_BYTES ||
            '\u0000' in script
        ) {
            throw Rejected("script", "Script must be at most 64 KiB and contain no NUL")
        }
        checkRunMs(runMs)
        val slotLock = slots.checkedIdentity()
        val lease = sessions.acquire(owner)
        val resource = sessions.reserve(lease, ResourceKind.SHELL, changing = true)
        val job =
            ShellJob(
                resource,
                sessions,
                outputs,
                ShellSpec(
                    executable = executable,
                    slotLock = slotLock,
                    script = script,
                    runMs = runMs,
                    background = background,
                ),
                checkWorker,
                jobs::trim,
            )
        val id = job.id
        jobs.start(job) { job.start() }
        return outcome("shell", Outcome.RUNNING, data = json("job" to id))
    }

    private fun find(
        owner: SessionOwner,
        args: JSONObject,
    ): ShellJob = jobs.find(owner, args.getString("job"))

    fun get(
        owner: SessionOwner,
        args: JSONObject,
    ): JSONObject = find(owner, args).view()

    fun cancel(
        owner: SessionOwner,
        args: JSONObject,
    ): JSONObject =
        find(owner, args).let { job ->
            job.cancel()
            job.view()
        }

    fun output(
        owner: SessionOwner,
        args: JSONObject,
    ): JSONObject {
        val job = find(owner, args)
        return outcome("job output", data = outputs.read(job.id, args))
    }

    fun retainedPairings(): Set<String> = jobs.snapshot().map { it.owner.pairing }.toSet()

    fun revoke(pairing: String) = jobs.revoke(pairing)

    fun prepareStop(action: () -> Unit) = jobs.prepareStop(action)

    fun snapshot(owner: SessionOwner?): JSONObject =
        slots.snapshot().put(
            "active",
            JSONArray(
                jobs
                    .snapshot()
                    .filter {
                        !it.ended && (owner == null || it.owner.sameSession(owner))
                    }.map { it.id },
            ),
        )

    companion object {
        private const val DEFAULT_RUN_MS = 10000L
        private const val SCRIPT_BYTES = 64 * 1024
    }
}

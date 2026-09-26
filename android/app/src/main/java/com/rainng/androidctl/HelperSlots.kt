package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException

internal data class SlotLockIdentity(
    val path: String,
    val device: Long,
    val inode: Long,
)

internal class HelperSlots(
    directory: File,
) {
    private val path = File(directory, "helpers.lock").path
    private val opened = runCatching { NativeFiles.openLockFile(path.toByteArray()) }

    fun checkedIdentity(): SlotLockIdentity {
        val fd = opened.getOrElse { throw Rejected("helperSlots", "Helper capacity lock $path unavailable: ${message(it)}") }
        val identity =
            try {
                NativeFiles.checkLockFile(fd, path.toByteArray())
            } catch (error: IOException) {
                throw Rejected("helperSlots", "Helper capacity lock $path unavailable: ${message(error)}").also { it.initCause(error) }
            }
        return SlotLockIdentity(path = path, device = identity[0], inode = identity[1])
    }

    fun snapshot(): JSONObject =
        runCatching {
            checkedIdentity()
            val owners = NativeFiles.slotOwners(opened.getOrThrow())
            json("pids" to JSONArray(owners.filter { it != 0 }))
        }.getOrElse { json("error" to message(it)) }
}

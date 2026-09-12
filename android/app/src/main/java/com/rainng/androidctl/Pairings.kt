package com.rainng.androidctl

import android.content.Context
import android.os.SystemClock
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.concurrent.TimeUnit

internal class Pairings(
    private val file: File,
    private val clock: () -> Long = SystemClock::elapsedRealtime,
    private val newCode: () -> String = { SecureRandom().nextInt(CODE_VALUES).toString().padStart(CODE_DIGITS, '0') },
) {
    private var entries = if (file.exists()) JSONObject(file.readText()) else JSONObject()
    private var window: Window? = null
    private var storageFailed = false

    class Window(
        val code: String,
        val expires: Long,
    ) {
        var failures = 0
    }

    @Synchronized fun open() {
        if (storageFailed) throw Rejected("pairingStore", "Pairing storage failed; restart the App after correcting storage")
        window = Window(newCode(), clock() + WINDOW_MS)
    }

    @Synchronized fun closeWindow() {
        window = null
    }

    @Synchronized fun display(): PairingDisplay? {
        val current = window ?: return null
        if (!valid(current)) return null
        return PairingDisplay(
            current.code,
            TimeUnit.MILLISECONDS.toSeconds((current.expires - clock()).coerceAtLeast(0L)),
        )
    }

    @Synchronized fun acceptCode(value: String): Window? {
        val current = window ?: return null
        if (value.isEmpty() || !valid(current)) return null
        return if (equal(current.code, value)) {
            current
        } else {
            current.failures++
            if (current.failures >= MAX_FAILURES) closeWindow()
            null
        }
    }

    @Synchronized fun register(
        value: Window,
        credential: String,
        name: String,
    ): String {
        if (!valid(value)) {
            throw Rejected("pairingExpired", "Pairing window is closed or consumed; explicitly show a new code on the device")
        }
        if (credential.length !in MIN_CREDENTIAL..MAX_CREDENTIAL) throw Rejected("credential", "Invalid credential size")
        if (name.isBlank() ||
            name.length > PAIRING_NAME_UNITS ||
            name.any { it.isISOControl() }
        ) {
            throw Rejected("pairingName", "Invalid host name")
        }
        val id = token()
        val updated =
            copy(entries).put(
                id,
                json(
                    "verifier" to sha256(credential.toByteArray()),
                    "created" to System.currentTimeMillis(),
                    "name" to name,
                ),
            )
        closeWindow()
        save(updated)
        return id
    }

    @Synchronized fun authenticate(authorization: String): String? {
        if (!authorization.startsWith("Bearer ") || storageFailed) return null
        val digest = sha256(authorization.removePrefix("Bearer ").toByteArray())
        return entries.keys().asSequence().firstOrNull { equal(entries.getJSONObject(it).getString("verifier"), digest) }
    }

    @Synchronized fun authorized(id: String): Boolean = entries.has(id) && !storageFailed

    @Synchronized fun name(id: String): String {
        if (!authorized(id)) throw Rejected("revoked", "This pairing has been revoked")
        return entries.getJSONObject(id).getString("name")
    }

    @Synchronized fun revoke(id: String) {
        val updated = copy(entries).apply { remove(id) }
        save(updated)
    }

    @Synchronized fun list(): List<PairingSummary> =
        entries
            .keys()
            .asSequence()
            .map {
                val entry = entries.getJSONObject(it)
                PairingSummary(it, entry.getString("name"), entry.getLong("created"))
            }.toList()

    // Failed persistence disables credentials until a successful save or reload.
    private fun save(updated: JSONObject) {
        try {
            persist(file, updated.toString().toByteArray())
            entries = updated
            storageFailed = false
        } catch (error: Exception) {
            storageFailed = true
            throw error
        }
    }

    private fun equal(
        first: String,
        second: String,
    ): Boolean = MessageDigest.isEqual(first.toByteArray(), second.toByteArray())

    private fun valid(value: Window): Boolean = window === value && clock() < value.expires && !storageFailed

    companion object {
        private var shared: Pairings? = null

        @Synchronized fun get(context: Context): Pairings =
            shared ?: Pairings(File(context.applicationContext.filesDir, "pairings.json")).also { shared = it }

        private const val CODE_VALUES = 1000000
        private const val CODE_DIGITS = 6
        const val MAX_FAILURES = 5
        const val WINDOW_MS = 600000L
        private const val MIN_CREDENTIAL = 40
        private const val MAX_CREDENTIAL = 128
        const val PAIRING_NAME_UNITS = 100
    }
}

internal data class PairingSummary(
    val id: String,
    val name: String,
    val created: Long,
) {
    fun toJson(): JSONObject = json("id" to id, "name" to name, "created" to created)
}

internal data class PairingDisplay(
    val code: String,
    val remainingSeconds: Long,
)

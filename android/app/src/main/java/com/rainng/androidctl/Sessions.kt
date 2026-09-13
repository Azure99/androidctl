package com.rainng.androidctl

import org.json.JSONObject

internal data class SessionOwner(
    val pairing: String,
    val name: String,
    val pairingName: String,
) {
    val sessionKey: Pair<String, String> get() = pairing to name

    fun sameSession(other: SessionOwner): Boolean = sessionKey == other.sessionKey
}

internal class Lease(
    val id: Long,
    val owner: SessionOwner,
    var touched: Long,
    val idleMs: Long,
)

internal class Sessions(
    private val now: () -> Long,
) {
    private var sequence = 0L
    private var current: Lease? = null
    private val revoked = mutableSetOf<String>()

    @get:Synchronized var stopped = false
        private set

    @Synchronized fun acquire(owner: SessionOwner): Lease {
        accepting()
        validate(owner)
        expire()
        val held = current
        if (held != null && !held.owner.sameSession(owner)) busy(held)
        val lease = held ?: Lease(++sequence, owner, now(), DEFAULT_IDLE_MS).also { current = it }
        lease.touched = now()
        return lease
    }

    @Synchronized fun open(
        owner: SessionOwner,
        idleMs: Long = DEFAULT_IDLE_MS,
    ): Lease {
        accepting()
        validate(owner)
        expire()
        val held = current
        if (held != null && !held.owner.sameSession(owner)) busy(held)
        if (held != null) invalidateLease()
        val lease = Lease(++sequence, owner, now(), idleMs)
        current = lease
        lease.touched = now()
        return lease
    }

    @Synchronized fun close(owner: SessionOwner) {
        validate(owner)
        val held = current ?: return
        if (!held.owner.sameSession(owner)) busy(held)
        invalidateLease()
    }

    @Synchronized fun takeover(owner: SessionOwner): String? {
        validate(owner)
        expire()
        val held = current
        if (held != null && held.owner.pairing != owner.pairing) busy(held)
        val previous = held?.owner?.name
        if (held != null) invalidateLease()
        acquire(owner)
        return previous
    }

    @Synchronized fun retainedPairings(): Set<String> = listOfNotNull(current?.owner?.pairing).toSet()

    @Synchronized fun revoke(pairing: String) {
        revoked.add(pairing)
        current?.takeIf { it.owner.pairing == pairing }?.let { invalidateLease() }
    }

    @Synchronized fun requireCurrent(lease: Lease) {
        accepting()
        validate(lease.owner)
        if (current !== lease) throw Rejected("sessionChanged", "The session was closed")
    }

    @Synchronized fun isCurrent(lease: Lease): Boolean = current === lease

    @Synchronized fun requireAuthorized(owner: SessionOwner) = validate(owner)

    @Synchronized fun stop() {
        stopped = true
    }

    private fun accepting() {
        if (stopped) throw Rejected("stopping", "Worker is stopping")
    }

    @Synchronized fun expire() {
        val held = current ?: return
        if (now() - held.touched >= held.idleMs) invalidateLease()
    }

    @Synchronized fun localStatus(): JSONObject {
        expire()
        return statusFields().put("leaseId", current?.id ?: JSONObject.NULL)
    }

    @Synchronized fun callerStatus(owner: SessionOwner): JSONObject {
        expire()
        return statusFields().put("callerState", callerState(owner))
    }

    private fun statusFields(): JSONObject {
        val held = current
        return json(
            "name" to held?.owner?.name,
            "pairingName" to held?.owner?.pairingName,
            "idleSeconds" to if (held == null) 0L else (now() - held.touched) / SECOND_MS,
        )
    }

    private fun callerState(owner: SessionOwner): String =
        when {
            current?.owner?.sameSession(owner) == true -> "active"
            else -> "none"
        }

    private fun invalidateLease() {
        current = null
    }

    private fun validate(owner: SessionOwner) {
        if (owner.pairing in revoked) throw Rejected("revoked", "This pairing has been revoked")
        if (owner.name.isBlank() || owner.name.length > MAX_NAME || owner.name.any { it.isISOControl() }) {
            throw Rejected("sessionName", "Session name must contain 1 to 64 printable characters")
        }
    }

    private fun busy(held: Lease): Nothing =
        throw Rejected(
            "busy",
            "busy: ${displayName(held.owner.pairingName)}/${displayName(held.owner.name)} " +
                "idle=${(now() - held.touched) / SECOND_MS}s",
        )

    companion object {
        const val DEFAULT_IDLE_MS = 600000L
        private const val SECOND_MS = 1000L
        private const val MAX_NAME = 64
    }
}

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

internal enum class ResourceKind(
    val wire: String,
) {
    UI("ui"),
    SHELL("shell"),
    FILE("file"),
}

internal class Resource(
    val lease: Lease,
    val kind: ResourceKind,
)

internal class Sessions(
    private val now: () -> Long,
    private val clearLeaseUi: (Lease) -> Unit,
) {
    private var sequence = 0L
    private var current: Lease? = null
    private val released = mutableSetOf<Pair<String, String>>()
    private val revoked = mutableSetOf<String>()
    private val resources = mutableSetOf<Resource>()

    @get:Synchronized var stopped = false
        private set

    @Synchronized fun acquire(owner: SessionOwner): Lease {
        accepting()
        validate(owner)
        expire()
        if (owner.sessionKey in released) {
            throw Rejected("sessionReleased", "Session released on device; run session open explicitly")
        }
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
        requireIdle()
        if (held != null) invalidateLease(held)
        val lease = Lease(++sequence, owner, now(), idleMs)
        current = lease
        released.remove(owner.sessionKey)
        lease.touched = now()
        return lease
    }

    @Synchronized fun close(owner: SessionOwner) {
        validate(owner)
        val held = current ?: return
        if (!held.owner.sameSession(owner)) busy(held)
        requireIdle()
        invalidateLease(held)
    }

    @Synchronized fun takeover(owner: SessionOwner): String? {
        validate(owner)
        expire()
        if (owner.sessionKey in released) {
            throw Rejected("sessionReleased", "Session released on device; run session open explicitly")
        }
        val held = current
        if (held != null && held.owner.pairing != owner.pairing) busy(held)
        requireIdle()
        val previous = held?.owner?.name
        if (held != null) invalidateLease(held)
        acquire(owner)
        return previous
    }

    @Synchronized fun releaseLocal(expected: Long) {
        val held = current ?: throw Rejected("sessionChanged", "There is no current session")
        if (held.id != expected) throw Rejected("sessionChanged", "The displayed session has changed; refresh first")
        released.add(held.owner.sessionKey)
        invalidateLease(held)
    }

    @Synchronized fun retainedPairings(): Set<String> =
        resources.map { it.lease.owner.pairing }.toSet() +
            listOfNotNull(current?.owner?.pairing)

    @Synchronized fun revoke(pairing: String) {
        revoked.add(pairing)
        current?.takeIf { it.owner.pairing == pairing }?.let(::invalidateLease)
    }

    @Synchronized fun reserve(
        lease: Lease,
        kind: ResourceKind,
        changing: Boolean,
    ): Resource {
        requireCurrent(lease)
        if (resources.any { it.kind == ResourceKind.UI } || (changing && resources.isNotEmpty())) {
            throw Rejected("busy", "Active ${resources.joinToString { it.kind.wire }} must finish")
        }
        return Resource(lease, kind).also { resources.add(it) }
    }

    @Synchronized fun finished(resource: Resource) {
        if (resources.remove(resource)) current?.touched = now()
    }

    @Synchronized fun closing(lease: Lease): Resource = Resource(lease, ResourceKind.UI).also { resources.add(it) }

    @Synchronized fun requireCurrent(lease: Lease) {
        accepting()
        validate(lease.owner)
        if (current !== lease) throw Rejected("sessionChanged", "The session was closed or released; take a new snapshot")
    }

    @Synchronized fun isCurrent(lease: Lease): Boolean = current === lease

    @Synchronized fun <T> withCurrentLease(
        lease: Lease,
        action: () -> T,
    ): T {
        requireCurrent(lease)
        return action()
    }

    @Synchronized fun requireAuthorized(owner: SessionOwner) = validate(owner)

    @Synchronized fun requireIdle() {
        if (resources.isNotEmpty()) throw Rejected("busy", "Active ${resources.joinToString { it.kind.wire }} must finish")
    }

    @Synchronized fun stop() {
        requireIdle()
        stopped = true
    }

    private fun accepting() {
        if (stopped) throw Rejected("stopping", "Worker is stopping")
    }

    @Synchronized fun expire() {
        val held = current ?: return
        if (resources.isEmpty() && now() - held.touched >= held.idleMs) invalidateLease(held)
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
            "idleSeconds" to if (held == null || resources.isNotEmpty()) 0L else (now() - held.touched) / SECOND_MS,
            "ui" to resources.count { it.kind == ResourceKind.UI },
            "shell" to resources.count { it.kind == ResourceKind.SHELL },
            "file" to resources.count { it.kind == ResourceKind.FILE },
        )
    }

    private fun callerState(owner: SessionOwner): String =
        when {
            owner.sessionKey in released -> "released"
            current?.owner?.sameSession(owner) == true -> "active"
            else -> "none"
        }

    private fun invalidateLease(held: Lease) {
        current = null
        clearLeaseUi(held)
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
                "idle=${if (resources.isEmpty()) (now() - held.touched) / SECOND_MS else 0}s",
        )

    companion object {
        const val DEFAULT_IDLE_MS = 600000L
        private const val SECOND_MS = 1000L
        private const val MAX_NAME = 64
    }
}

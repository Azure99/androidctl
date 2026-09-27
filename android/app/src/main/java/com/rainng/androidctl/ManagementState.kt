package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject

internal data class ManagementState(
    val gateway: GatewayStatus = GatewayStatus(GatewayState.STOPPED),
    val executor: WorkerStatus? = null,
    val error: String? = null,
    val deviceId: String? = null,
    val pendingRevocations: List<String> = emptyList(),
) {
    fun toJson(pairings: List<PairingSummary>?): JSONObject =
        json(
            "gateway" to gateway.toJson(),
            "executor" to executor?.toJson(),
            "error" to error,
            "deviceId" to deviceId,
            "pairings" to pairings?.let { JSONArray(it.map(PairingSummary::toJson)) },
            "pendingRevocations" to JSONArray(pendingRevocations),
        )
}

internal enum class GatewayState {
    STARTING,
    LISTENING,
    STOPPING,
    STOPPED,
    FAILED,
    ;

    val wire: String get() = name.lowercase()
}

internal data class GatewayStatus(
    val state: GatewayState,
    val error: String? = null,
    val traffic: GatewayTraffic? = null,
) {
    fun toJson(): JSONObject =
        json(
            "state" to state.wire,
            "error" to error,
            "port" to traffic?.let { Limits.PORT },
            "connections" to traffic?.connections,
            "clients" to traffic?.clients,
            "activeCalls" to traffic?.activeCalls,
            "activeQueries" to traffic?.activeQueries,
            "queuedCalls" to traffic?.queuedCalls,
            "queuedQueries" to traffic?.queuedQueries,
            "tlsActive" to traffic?.tlsActive,
            "tlsQueued" to traffic?.tlsQueued,
        )
}

internal data class GatewayTraffic(
    val connections: Int,
    val clients: Int,
    val activeCalls: Int,
    val activeQueries: Int,
    val queuedCalls: Int,
    val queuedQueries: Int,
    val tlsActive: Int,
    val tlsQueued: Int,
)

// Immutable Binder result plus the fields used by local management actions.
internal data class WorkerStatus(
    private val raw: String,
    val version: String,
    val uid: Int,
    val workerId: String,
    val session: SessionStatus,
    val helpers: HelperStatus,
    val diagnostics: WorkerDiagnostics,
) {
    fun toJson(): JSONObject = JSONObject(raw)

    companion object {
        fun fromJson(value: JSONObject): WorkerStatus =
            WorkerStatus(
                value.toString(),
                value.getString("version"),
                value.getInt("uid"),
                value.getString("workerId"),
                SessionStatus.fromJson(value.getJSONObject("session")),
                HelperStatus.fromJson(value.getJSONObject("helpers")),
                WorkerDiagnostics.fromJson(value.getJSONObject("diagnostics")),
            )
    }
}

internal data class SessionStatus(
    val name: String?,
    val pairingName: String?,
    val idleSeconds: Long,
    val shell: Int,
    val file: Int,
    val leaseId: Long?,
) {
    companion object {
        fun fromJson(value: JSONObject): SessionStatus =
            SessionStatus(
                value.nullableString("name"),
                value.nullableString("pairingName"),
                value.getLong("idleSeconds"),
                value.getInt("shell"),
                value.getInt("file"),
                if (value.isNull("leaseId")) null else value.getLong("leaseId"),
            )
    }
}

internal data class HelperStatus(
    val active: List<String>,
) {
    companion object {
        fun fromJson(value: JSONObject): HelperStatus =
            HelperStatus(value.getJSONArray("active").let { active -> List(active.length(), active::getString) })
    }
}

internal data class WorkerDiagnostics(
    val maintenance: Map<String, String>,
) {
    companion object {
        fun fromJson(value: JSONObject): WorkerDiagnostics =
            WorkerDiagnostics(
                value.getJSONObject("maintenance").let { entries -> entries.keys().asSequence().associateWith(entries::getString) },
            )
    }
}

private fun JSONObject.nullableString(key: String): String? = if (isNull(key)) null else getString(key)

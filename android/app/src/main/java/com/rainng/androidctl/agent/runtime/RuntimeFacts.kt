package com.rainng.androidctl.agent.runtime

internal data class AuthFacts(
    val currentToken: String? = null,
    val blocked: Boolean = false,
    val blockedMessage: String? = null,
    val available: Boolean = false,
)

internal data class ForegroundFacts(
    val hintPackageName: String? = null,
    val hintActivityName: String? = null,
    val generation: Long = 0L,
)

internal data class RuntimeFacts(
    val serverPhase: ServerPhase = ServerPhase.STOPPED,
    val auth: AuthFacts = AuthFacts(),
    val accessibilityEnabled: Boolean = false,
    val accessibilityAttached: Boolean = false,
    val foreground: ForegroundFacts = ForegroundFacts(),
)

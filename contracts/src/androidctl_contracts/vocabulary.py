"""Centralized frozen vocabularies for shared contracts."""

from __future__ import annotations

from enum import Enum


class RuntimeStatus(str, Enum):
    NEW = "new"
    BOOTSTRAPPING = "bootstrapping"
    CONNECTED = "connected"
    READY = "ready"
    BROKEN = "broken"
    CLOSED = "closed"


class PublicResultCategory(str, Enum):
    OBSERVE = "observe"
    OPEN = "open"
    TRANSITION = "transition"
    WAIT = "wait"


class PublicResultFamily(str, Enum):
    SEMANTIC = "semantic"
    RETAINED = "retained"
    LIST_APPS = "listApps"


class RetainedEnvelopeKind(str, Enum):
    BOOTSTRAP = "bootstrap"
    ARTIFACT = "artifact"
    LIFECYCLE = "lifecycle"


class PayloadMode(str, Enum):
    NONE = "none"
    FULL = "full"


class ExecutionOutcome(str, Enum):
    NOT_APPLICABLE = "notApplicable"
    NOT_ATTEMPTED = "notAttempted"
    DISPATCHED = "dispatched"
    UNKNOWN = "unknown"


class ContinuityStatus(str, Enum):
    NONE = "none"
    STABLE = "stable"
    STALE = "stale"


class ObservationQuality(str, Enum):
    NONE = "none"
    AUTHORITATIVE = "authoritative"


class SemanticResultCode(str, Enum):
    REF_STALE = "REF_STALE"
    WAIT_TIMEOUT = "WAIT_TIMEOUT"
    TARGET_BLOCKED = "TARGET_BLOCKED"
    TARGET_NOT_ACTIONABLE = "TARGET_NOT_ACTIONABLE"
    OPEN_FAILED = "OPEN_FAILED"
    ACTION_NOT_CONFIRMED = "ACTION_NOT_CONFIRMED"
    TYPE_NOT_CONFIRMED = "TYPE_NOT_CONFIRMED"
    SUBMIT_NOT_CONFIRMED = "SUBMIT_NOT_CONFIRMED"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    POST_ACTION_OBSERVATION_LOST = "POST_ACTION_OBSERVATION_LOST"


__all__ = [
    "ContinuityStatus",
    "ExecutionOutcome",
    "ObservationQuality",
    "PayloadMode",
    "PublicResultCategory",
    "PublicResultFamily",
    "RetainedEnvelopeKind",
    "RuntimeStatus",
    "SemanticResultCode",
]

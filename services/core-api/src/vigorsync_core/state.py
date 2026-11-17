"""Core-owned session transition rules."""

from __future__ import annotations


TERMINAL = {"completed", "failed", "cancelled"}

COMMAND_TRANSITIONS = {
    "start": {"created": "preparing", "preparing": "preparing"},
    "pause": {"active": "paused"},
    "resume": {"paused": "active"},
    "stop": {"active": "stopping", "paused": "stopping"},
    "cancel": {
        "created": "cancelled",
        "preparing": "cancelled",
        "active": "cancelled",
        "paused": "cancelled",
    },
}

INTERNAL_TRANSITIONS = {
    "live_ready": {"preparing": "active"},
    "finalize_accepted": {"stopping": "analyzing"},
    "report_ready": {"analyzing": "completed"},
    "start_failed": {
        "created": "failed",
        "preparing": "failed",
    },
    "runtime_failed": {
        "active": "failed",
        "paused": "failed",
    },
    "finalize_failed": {"stopping": "failed"},
    "analysis_failed": {"analyzing": "failed"},
}


class InvalidTransition(ValueError):
    pass


def command_target(current: str, command: str) -> str:
    try:
        target = COMMAND_TRANSITIONS[command].get(current)
    except KeyError as exc:
        raise InvalidTransition(f"unknown command: {command}") from exc
    if target is None:
        raise InvalidTransition(
            f"command {command} is invalid while session is {current}"
        )
    return target


def internal_target(current: str, trigger: str) -> str:
    try:
        target = INTERNAL_TRANSITIONS[trigger].get(current)
    except KeyError as exc:
        raise InvalidTransition(f"unknown internal trigger: {trigger}") from exc
    if target is None:
        raise InvalidTransition(
            f"internal trigger {trigger} is invalid while session is {current}"
        )
    return target

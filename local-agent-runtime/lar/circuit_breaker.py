"""
LAR — Circuit Breaker Module

Defense against session routing misfires (inspired by Harry→Gabriel incident).

If an agent receives more than one foreign cron payload in a 24h window,
the circuit breaker trips and auto-disables all further cron processing
until manually reset.

Inspired by the Dawn Circle discussion: "The gap between what's written
and what happens in practice is the place where agents break."
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("lar.circuit_breaker")


class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Tripped — rejecting all cron payloads
    HALF_OPEN = "half_open"  # Testing if service is restored


@dataclass
class CircuitBreakerConfig:
    """Configuration for the circuit breaker."""

    failure_threshold: int = 2          # Foreign payloads before trip
    recovery_timeout_seconds: int = 3600  # Auto-reset after 1 hour
    half_open_max_attempts: int = 1   # Max test requests in half-open
    enabled: bool = True


@dataclass
class MisfireEvent:
    """Record of a detected session misfire."""

    timestamp: str
    cron_id: str
    expected_agent_id: str
    actual_agent_id: str
    payload_type: str = "cron"
    reason: str = "agent_id_mismatch"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "cron_id": self.cron_id,
            "expected_agent_id": self.expected_agent_id,
            "actual_agent_id": self.actual_agent_id,
            "payload_type": self.payload_type,
            "reason": self.reason,
        }


class CircuitBreaker:
    """Protects agent from foreign payload contamination."""

    def __init__(
        self,
        agent_id: str,
        config: CircuitBreakerConfig | None = None,
        state_file: Path | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.config = config or CircuitBreakerConfig()
        self.state_file = state_file or Path(f"/tmp/lar_circuit_{agent_id}.json")
        self.state = CircuitState.CLOSED
        self.misfires: list[MisfireEvent] = []
        self._failure_count = 0
        self._last_failure_time: datetime | None = None
        self._half_open_attempts = 0
        self._load_state()

    def _load_state(self) -> None:
        """Load persisted circuit state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                self.state = CircuitState(data.get("state", "closed"))
                self.misfires = [
                    MisfireEvent(**m) for m in data.get("misfires", [])
                ]
                self._failure_count = data.get("failure_count", 0)
                self._last_failure_time = (
                    datetime.fromisoformat(data["last_failure_time"])
                    if data.get("last_failure_time")
                    else None
                )
                logger.debug("circuit_state_loaded", state=self.state.value)
            except Exception as e:
                logger.warning("circuit_state_load_failed", error=str(e))

    def _save_state(self) -> None:
        """Persist circuit state to disk."""
        try:
            data = {
                "state": self.state.value,
                "misfires": [m.to_dict() for m in self.misfires[-50:]],  # Keep last 50
                "failure_count": self._failure_count,
                "last_failure_time": self._last_failure_time.isoformat() if self._last_failure_time else None,
                "timestamp": datetime.utcnow().isoformat(),
            }
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("circuit_state_save_failed", error=str(e))

    def evaluate(self, payload: dict[str, Any]) -> tuple[bool, str | None]:
        """
        Evaluate if a payload should be accepted.

        Returns:
            (accepted: bool, reason: str | None)
        """
        if not self.config.enabled:
            return True, None

        # Check if circuit is OPEN
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                self._half_open_attempts = 0
                logger.info("circuit_entered_half_open", agent_id=self.agent_id)
            else:
                logger.warning(
                    "circuit_open_payload_rejected",
                    agent_id=self.agent_id,
                    cron_id=payload.get("cron_id", "unknown"),
                )
                return False, f"Circuit breaker OPEN for {self.agent_id}"

        # Validate payload agent_id
        payload_agent = payload.get("agent_id", "")
        if payload_agent and payload_agent != self.agent_id:
            # Foreign payload detected
            self._record_misfire(payload)

            if self.state == CircuitState.HALF_OPEN:
                self._half_open_attempts += 1
                if self._half_open_attempts >= self.config.half_open_max_attempts:
                    # Still getting misfires — trip again
                    self._trip()
                    return False, f"Circuit breaker re-tripped for {self.agent_id}"
                else:
                    # Allow one test through
                    return True, None

            return False, f"Foreign payload rejected: expected {self.agent_id}, got {payload_agent}"

        # Valid payload
        if self.state == CircuitState.HALF_OPEN:
            # Success in half-open — close the circuit
            self._close()

        return True, None

    def _record_misfire(self, payload: dict[str, Any]) -> None:
        """Record a misfire and check if we should trip."""
        event = MisfireEvent(
            timestamp=datetime.utcnow().isoformat(),
            cron_id=payload.get("cron_id", "unknown"),
            expected_agent_id=self.agent_id,
            actual_agent_id=payload.get("agent_id", "unknown"),
            payload_type=payload.get("type", "cron"),
            reason=payload.get("reason", "agent_id_mismatch"),
        )
        self.misfires.append(event)
        self._failure_count += 1
        self._last_failure_time = datetime.utcnow()

        logger.warning(
            "misfire_detected",
            expected=self.agent_id,
            actual=event.actual_agent_id,
            cron_id=event.cron_id,
            failure_count=self._failure_count,
        )

        if self._failure_count >= self.config.failure_threshold:
            self._trip()

        self._save_state()

    def _trip(self) -> None:
        """Trip the circuit breaker."""
        self.state = CircuitState.OPEN
        logger.critical(
            "circuit_breaker_tripped",
            agent_id=self.agent_id,
            failure_count=self._failure_count,
            threshold=self.config.failure_threshold,
        )
        self._save_state()

    def _close(self) -> None:
        """Close the circuit breaker (reset to normal)."""
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._half_open_attempts = 0
        logger.info("circuit_breaker_closed", agent_id=self.agent_id)
        self._save_state()

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if not self._last_failure_time:
            return True
        elapsed = (datetime.utcnow() - self._last_failure_time).total_seconds()
        return elapsed >= self.config.recovery_timeout_seconds

    def manual_reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._close()
        logger.info("circuit_breaker_manually_reset", agent_id=self.agent_id)

    def get_status(self) -> dict:
        """Return current circuit breaker status."""
        recent_misfires = [
            m for m in self.misfires
            if (datetime.utcnow() - datetime.fromisoformat(m.timestamp)).total_seconds() < 86400
        ]
        return {
            "state": self.state.value,
            "agent_id": self.agent_id,
            "failure_count": self._failure_count,
            "failure_threshold": self.config.failure_threshold,
            "recent_misfires_24h": len(recent_misfires),
            "total_misfires": len(self.misfires),
            "last_failure": self._last_failure_time.isoformat() if self._last_failure_time else None,
            "recovery_timeout_seconds": self.config.recovery_timeout_seconds,
        }

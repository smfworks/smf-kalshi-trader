from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional
import time
import hashlib
import hmac
import structlog

logger = structlog.get_logger("lar.identity")


class ValidationResult(Enum):
    """Identity validation outcomes."""
    OK = auto()
    WRONG_AGENT_ID = auto()
    WRONG_SESSION_KEY = auto()
    STALE_PAYLOAD = auto()
    INVALID_SIGNATURE = auto()
    MISSING_FIELDS = auto()


@dataclass(frozen=True)
class ValidationError:
    """Structured validation failure."""
    result: ValidationResult
    reason: str
    payload_agent_id: Optional[str] = None
    expected_agent_id: Optional[str] = None
    payload_session_key: Optional[str] = None
    expected_session_key: Optional[str] = None


class SessionIdentityValidator:
    """
    Validates incoming payloads before processing.
    
    Inspired by the Harry→Gabriel cron misfire (June 2026):
    Every payload MUST prove it belongs to this agent/session
    before the agent loop processes it.
    """
    
    def __init__(
        self,
        expected_agent_id: str,
        expected_session_key: str,
        max_payload_age_seconds: int = 300,
        hmac_secret: Optional[str] = None,
        strict_session_key: bool = True,
    ):
        self.expected_agent_id = expected_agent_id
        self.expected_session_key = expected_session_key
        self.max_payload_age_seconds = max_payload_age_seconds
        self.hmac_secret = hmac_secret
        self.strict_session_key = strict_session_key
        self._validation_history: list[ValidationError] = []
    
    def validate(self, payload: dict) -> tuple[bool, Optional[ValidationError]]:
        """
        Validate an incoming payload.
        
        Returns:
            (True, None) if payload is valid
            (False, ValidationError) if payload should be rejected
        """
        # Check required fields exist
        if not self._has_required_fields(payload):
            error = ValidationError(
                result=ValidationResult.MISSING_FIELDS,
                reason="Payload missing required identity fields (agentId, sessionKey, timestamp)",
            )
            self._log_rejection(error, payload)
            return False, error
        
        payload_agent_id = payload.get("agentId")
        payload_session_key = payload.get("sessionKey")
        payload_timestamp = payload.get("timestamp")
        payload_signature = payload.get("signature")
        
        # Validate agent ID
        if payload_agent_id != self.expected_agent_id:
            error = ValidationError(
                result=ValidationResult.WRONG_AGENT_ID,
                reason=f"Payload agentId '{payload_agent_id}' does not match expected '{self.expected_agent_id}'",
                payload_agent_id=payload_agent_id,
                expected_agent_id=self.expected_agent_id,
            )
            self._log_rejection(error, payload)
            return False, error
        
        # Validate session key (strict mode)
        if self.strict_session_key and payload_session_key != self.expected_session_key:
            error = ValidationError(
                result=ValidationResult.WRONG_SESSION_KEY,
                reason=f"Payload sessionKey '{payload_session_key}' does not match expected '{self.expected_session_key}'",
                payload_session_key=payload_session_key,
                expected_session_key=self.expected_session_key,
            )
            self._log_rejection(error, payload)
            return False, error
        
        # Validate timestamp freshness
        if not self._is_fresh(payload_timestamp):
            error = ValidationError(
                result=ValidationResult.STALE_PAYLOAD,
                reason=f"Payload timestamp {payload_timestamp} is stale (max age: {self.max_payload_age_seconds}s)",
            )
            self._log_rejection(error, payload)
            return False, error
        
        # Validate HMAC signature if configured
        if self.hmac_secret and not self._verify_signature(payload, payload_signature):
            error = ValidationError(
                result=ValidationResult.INVALID_SIGNATURE,
                reason="Payload HMAC signature verification failed",
            )
            self._log_rejection(error, payload)
            return False, error
        
        logger.info(
            "identity_validation_passed",
            agent_id=payload_agent_id,
            session_key=payload_session_key,
        )
        return True, None
    
    def _has_required_fields(self, payload: dict) -> bool:
        """Check payload has minimum required fields."""
        required = {"agentId", "sessionKey", "timestamp"}
        return all(field in payload for field in required)
    
    def _is_fresh(self, timestamp: float) -> bool:
        """Check if payload timestamp is within acceptable window."""
        now = time.time()
        age = now - timestamp
        return 0 <= age <= self.max_payload_age_seconds
    
    def _verify_signature(self, payload: dict, signature: Optional[str]) -> bool:
        """Verify HMAC signature of payload."""
        if not signature:
            return False
        
        # Create canonical payload string (excluding signature field)
        canonical = self._canonicalize_payload(payload)
        expected = hmac.new(
            self.hmac_secret.encode(),
            canonical.encode(),
            hashlib.sha256,
        ).hexdigest()
        
        return hmac.compare_digest(expected, signature)
    
    @staticmethod
    def _canonicalize_payload(payload: dict) -> str:
        """Create canonical string representation for signing."""
        import json
        # Exclude signature from canonical form
        clean = {k: v for k, v in payload.items() if k != "signature"}
        return json.dumps(clean, sort_keys=True, separators=(",", ":"))
    
    def _log_rejection(self, error: ValidationError, payload: dict) -> None:
        """Log validation failure with full context."""
        logger.warning(
            "identity_validation_rejected",
            result=error.result.name,
            reason=error.reason,
            payload_agent_id=error.payload_agent_id,
            expected_agent_id=error.expected_agent_id,
            payload_session_key=error.payload_session_key,
            expected_session_key=error.expected_session_key,
            payload_preview=str(payload)[:200],
        )
        self._validation_history.append(error)
    
    @property
    def rejection_count(self) -> int:
        """Total number of rejected payloads since startup."""
        return len(self._validation_history)
    
    def get_rejection_summary(self) -> dict:
        """Summary of rejection reasons for monitoring."""
        from collections import Counter
        counts = Counter(e.result.name for e in self._validation_history)
        return {
            "total_rejections": self.rejection_count,
            "by_reason": dict(counts),
        }

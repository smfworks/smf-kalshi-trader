"""
LAR — Health Check & Monitoring Module

Self-diagnostic capabilities for the Local Agent Runtime:
- Ollama endpoint connectivity
- Tool registry integrity
- Checkpoint store consistency
- Session identity validation
- Disk space and resource availability
- Cron misfire detection (inspired by Harry→Gabriel incident)

Produces structured health reports compatible with observability dashboards.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import structlog
import httpx

from lar.config import RuntimeConfig
from lar.checkpoint import CheckpointStore

logger = structlog.get_logger("lar.health")


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class CheckResult:
    """Result of a single health check."""

    name: str
    status: HealthStatus
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "details": self.details,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "timestamp": self.timestamp,
        }


@dataclass
class HealthReport:
    """Aggregated health report for the entire runtime."""

    overall: HealthStatus
    checks: list[CheckResult]
    agent_id: str = "unknown"
    version: str = "1.0.0"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    uptime_seconds: float = 0.0
    session_count: int = 0
    last_cron_misfire: str | None = None

    def to_dict(self) -> dict:
        return {
            "overall": self.overall.value,
            "agent_id": self.agent_id,
            "version": self.version,
            "timestamp": self.timestamp,
            "uptime_seconds": self.uptime_seconds,
            "session_count": self.session_count,
            "last_cron_misfire": self.last_cron_misfire,
            "checks": [c.to_dict() for c in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


class HealthMonitor:
    """Runs health checks and produces structured reports."""

    def __init__(
        self,
        config: RuntimeConfig,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        self.config = config
        self.checkpoint_store = checkpoint_store
        self._start_time = datetime.utcnow()
        self._misfire_log: list[dict] = []
        self._session_count = 0

    # ── Individual Check Methods ──────────────────────────────────────

    async def check_ollama(self) -> CheckResult:
        """Verify Ollama endpoint is reachable and responsive."""
        start = asyncio.get_event_loop().time()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.config.model.base_url}/api/tags"
                )
                latency = (asyncio.get_event_loop().time() - start) * 1000
                if resp.status_code == 200:
                    data = resp.json()
                    models = data.get("models", [])
                    return CheckResult(
                        name="ollama_connectivity",
                        status=HealthStatus.HEALTHY,
                        details={
                            "models_available": len(models),
                            "primary_model": self.config.model.model,
                            "endpoint": self.config.model.base_url,
                        },
                        latency_ms=round(latency, 2),
                    )
                else:
                    return CheckResult(
                        name="ollama_connectivity",
                        status=HealthStatus.UNHEALTHY,
                        details={"status_code": resp.status_code},
                        latency_ms=round(latency, 2),
                        error=f"HTTP {resp.status_code}",
                    )
        except Exception as e:
            latency = (asyncio.get_event_loop().time() - start) * 1000
            return CheckResult(
                name="ollama_connectivity",
                status=HealthStatus.UNHEALTHY,
                latency_ms=round(latency, 2),
                error=str(e),
            )

    async def check_tools(self) -> CheckResult:
        """Verify tool registry integrity."""
        from lar.tools import ToolRegistry
        from lar.tools.builtin import register_builtin_tools

        registry = ToolRegistry()
        try:
            register_builtin_tools(registry)
            tools = registry.get_tool_names()
            return CheckResult(
                name="tool_registry",
                status=HealthStatus.HEALTHY,
                details={
                    "tools_registered": len(tools),
                    "tools": tools,
                },
            )
        except Exception as e:
            return CheckResult(
                name="tool_registry",
                status=HealthStatus.UNHEALTHY,
                error=str(e),
            )

    async def check_checkpoint_store(self) -> CheckResult:
        """Verify checkpoint store is readable and writable."""
        if not self.checkpoint_store:
            return CheckResult(
                name="checkpoint_store",
                status=HealthStatus.UNKNOWN,
                details={"reason": "no checkpoint_store configured"},
            )
        try:
            # Try to list incomplete tasks (lightweight read)
            tasks = await self.checkpoint_store.incomplete_tasks()
            return CheckResult(
                name="checkpoint_store",
                status=HealthStatus.HEALTHY,
                details={
                    "db_path": str(self.checkpoint_store.db_path),
                    "incomplete_tasks": len(tasks),
                },
            )
        except Exception as e:
            return CheckResult(
                name="checkpoint_store",
                status=HealthStatus.UNHEALTHY,
                error=str(e),
            )

    async def check_identity_validator(self) -> CheckResult:
        """Verify identity validation is operational."""
        from lar.identity import SessionIdentityValidator

        try:
            validator = SessionIdentityValidator(self.config.agent_id)
            # Test with a valid payload (self-matching)
            valid_payload = {
                "agent_id": self.config.agent_id,
                "session_key": f"agent:{self.config.agent_id}:main",
                "timestamp": datetime.utcnow().isoformat(),
            }
            result, _ = validator.validate(valid_payload)
            return CheckResult(
                name="identity_validator",
                status=HealthStatus.HEALTHY if result else HealthStatus.DEGRADED,
                details={
                    "agent_id": self.config.agent_id,
                    "self_validation": result,
                },
            )
        except Exception as e:
            return CheckResult(
                name="identity_validator",
                status=HealthStatus.UNHEALTHY,
                error=str(e),
            )

    async def check_disk_space(self) -> CheckResult:
        """Check available disk space for checkpoint DB and logs."""
        import shutil

        try:
            stat = shutil.disk_usage(self.config.workspace_dir)
            total_gb = stat.total / (1024**3)
            free_gb = stat.free / (1024**3)
            used_pct = (stat.used / stat.total) * 100

            status = HealthStatus.HEALTHY
            if used_pct > 90:
                status = HealthStatus.UNHEALTHY
            elif used_pct > 80:
                status = HealthStatus.DEGRADED

            return CheckResult(
                name="disk_space",
                status=status,
                details={
                    "total_gb": round(total_gb, 2),
                    "free_gb": round(free_gb, 2),
                    "used_percent": round(used_pct, 1),
                },
            )
        except Exception as e:
            return CheckResult(
                name="disk_space",
                status=HealthStatus.UNKNOWN,
                error=str(e),
            )

    async def check_cron_misfires(self) -> CheckResult:
        """Check for recent cron routing misfires (inspired by Harry→Gabriel)."""
        if not self._misfire_log:
            return CheckResult(
                name="cron_misfire_detector",
                status=HealthStatus.HEALTHY,
                details={"recent_misfires": 0},
            )

        # Count misfires in last 24h
        recent = [
            m for m in self._misfire_log
            if (datetime.utcnow() - datetime.fromisoformat(m["timestamp"])).total_seconds() < 86400
        ]

        status = HealthStatus.HEALTHY
        if len(recent) >= 2:
            status = HealthStatus.UNHEALTHY
        elif len(recent) == 1:
            status = HealthStatus.DEGRADED

        return CheckResult(
            name="cron_misfire_detector",
            status=status,
            details={
                "recent_misfires_24h": len(recent),
                "total_logged": len(self._misfire_log),
                "last_misfire": self._misfire_log[-1]["timestamp"] if self._misfire_log else None,
            },
        )

    # ── Aggregation ─────────────────────────────────────────────────

    async def run_all_checks(self) -> HealthReport:
        """Execute all health checks and produce a report."""
        checks = await asyncio.gather(
            self.check_ollama(),
            self.check_tools(),
            self.check_checkpoint_store(),
            self.check_identity_validator(),
            self.check_disk_space(),
            self.check_cron_misfires(),
        )

        # Determine overall status: worst wins
        overall = HealthStatus.HEALTHY
        for check in checks:
            if check.status == HealthStatus.UNHEALTHY:
                overall = HealthStatus.UNHEALTHY
                break
            elif check.status == HealthStatus.DEGRADED and overall != HealthStatus.UNHEALTHY:
                overall = HealthStatus.DEGRADED
            elif check.status == HealthStatus.UNKNOWN and overall == HealthStatus.HEALTHY:
                overall = HealthStatus.DEGRADED

        uptime = (datetime.utcnow() - self._start_time).total_seconds()

        report = HealthReport(
            overall=overall,
            checks=list(checks),
            agent_id=self.config.agent_id,
            timestamp=datetime.utcnow().isoformat(),
            uptime_seconds=round(uptime, 2),
            session_count=self._session_count,
            last_cron_misfire=self._misfire_log[-1]["timestamp"] if self._misfire_log else None,
        )

        logger.info(
            "health_report_generated",
            overall=overall.value,
            checks_passed=sum(1 for c in checks if c.status == HealthStatus.HEALTHY),
            checks_failed=sum(1 for c in checks if c.status == HealthStatus.UNHEALTHY),
        )

        return report

    # ── Cron Misfire Tracking ─────────────────────────────────────────

    def record_misfire(self, cron_id: str, expected_agent: str, actual_session: str) -> None:
        """Log a cron routing misfire for the health monitor to track."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "cron_id": cron_id,
            "expected_agent": expected_agent,
            "actual_session": actual_session,
        }
        self._misfire_log.append(entry)
        logger.warning("cron_misfire_recorded", **entry)

    def record_session_created(self) -> None:
        """Increment session count."""
        self._session_count += 1

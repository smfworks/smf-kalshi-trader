"""Model lifecycle management for LAR.

Tracks model availability, deprecation warnings, behavioral drift, and
capability shocks. Can simulate a Fable-style deprecation event for testing
resilience without touching the actual LLM backend.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("lar.model_lifecycle")


@dataclass
class ModelRecord:
    """Snapshot of a model's status and health."""
    model_id: str
    provider: str
    status: str  # "active", "deprecated", "unavailable", "drift_detected"
    first_seen_at: str
    last_seen_at: str
    last_price_check_at: Optional[str] = None
    deprecation_notice_at: Optional[str] = None
    estimated_eol_at: Optional[str] = None  # end-of-life date (YYYY-MM-DD)
    notes: list[str] = field(default_factory=list)


class ModelLifecycle:
    """Monitor and react to model lifecycle events."""

    def __init__(
        self,
        state_path: Path = Path("model_lifecycle.json"),
        deprecation_warning_days: int = 14,
        stale_price_days: int = 30,
    ):
        self.state_path = state_path
        self.deprecation_warning_days = deprecation_warning_days
        self.stale_price_days = stale_price_days
        self.models: dict[str, ModelRecord] = {}
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            for m in raw.get("models", []):
                rec = ModelRecord(**m)
                self.models[rec.model_id] = rec
            logger.info("lifecycle_state_loaded", count=len(self.models), path=str(self.state_path))
        except Exception as e:
            logger.warning("lifecycle_state_load_failed", error=str(e))

    def _save_state(self) -> None:
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "models": [
                {
                    "model_id": m.model_id,
                    "provider": m.provider,
                    "status": m.status,
                    "first_seen_at": m.first_seen_at,
                    "last_seen_at": m.last_seen_at,
                    "last_price_check_at": m.last_price_check_at,
                    "deprecation_notice_at": m.deprecation_notice_at,
                    "estimated_eol_at": m.estimated_eol_at,
                    "notes": m.notes,
                }
                for m in self.models.values()
            ],
        }
        self.state_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        logger.info("lifecycle_state_saved", path=str(self.state_path))

    def register_or_update(
        self,
        model_id: str,
        provider: str,
        status: str = "active",
        price_check: bool = False,
        note: Optional[str] = None,
    ) -> ModelRecord:
        now = datetime.now(timezone.utc).isoformat()
        if model_id in self.models:
            rec = self.models[model_id]
            rec.last_seen_at = now
            if status != rec.status:
                rec.notes.append(f"Status changed from {rec.status} to {status} at {now}")
                rec.status = status
            if price_check:
                rec.last_price_check_at = now
            if note:
                rec.notes.append(f"{now}: {note}")
        else:
            self.models[model_id] = ModelRecord(
                model_id=model_id,
                provider=provider,
                status=status,
                first_seen_at=now,
                last_seen_at=now,
                last_price_check_at=now if price_check else None,
                notes=[f"{now}: {note}"] if note else [],
            )
        self._save_state()
        return self.models[model_id]

    def mark_deprecated(
        self,
        model_id: str,
        estimated_eol_at: Optional[str] = None,
        reason: str = "Provider deprecation notice",
    ) -> Optional[ModelRecord]:
        now = datetime.now(timezone.utc).isoformat()
        rec = self.models.get(model_id)
        if not rec:
            logger.warning("cannot_deprecate_unknown_model", model_id=model_id)
            return None
        rec.status = "deprecated"
        rec.deprecation_notice_at = now
        rec.estimated_eol_at = estimated_eol_at
        rec.notes.append(f"{now}: Deprecation - {reason}")
        self._save_state()
        logger.warning("model_deprecated", model_id=model_id, eol=estimated_eol_at)
        return rec

    def check_health(self) -> dict:
        """Return lifecycle health summary and alerts."""
        now = datetime.now(timezone.utc)
        alerts: list[dict] = []
        active: list[str] = []
        deprecated: list[str] = []
        unavailable: list[str] = []
        drift_detected: list[str] = []
        stale_price: list[str] = []

        for rec in self.models.values():
            if rec.status == "active":
                active.append(rec.model_id)
            elif rec.status == "deprecated":
                deprecated.append(rec.model_id)
            elif rec.status == "unavailable":
                unavailable.append(rec.model_id)
            elif rec.status == "drift_detected":
                drift_detected.append(rec.model_id)

            if rec.estimated_eol_at:
                eol = datetime.fromisoformat(rec.estimated_eol_at).replace(tzinfo=timezone.utc)
                days_to_eol = (eol - now).total_seconds() / 86400
                if days_to_eol <= self.deprecation_warning_days:
                    alerts.append(
                        {
                            "severity": "critical" if days_to_eol <= 7 else "warning",
                            "model_id": rec.model_id,
                            "message": f"Model {rec.model_id} EOL in {int(days_to_eol)} days ({rec.estimated_eol_at})",
                        }
                    )

            if rec.last_price_check_at:
                last_price = datetime.fromisoformat(rec.last_price_check_at).replace(tzinfo=timezone.utc)
                days_since_price = (now - last_price).total_seconds() / 86400
                if days_since_price > self.stale_price_days:
                    stale_price.append(rec.model_id)

        if deprecated:
            for mid in deprecated:
                alerts.append({"severity": "warning", "model_id": mid, "message": f"Model {mid} is deprecated"})

        if stale_price:
            for mid in stale_price:
                alerts.append({"severity": "info", "model_id": mid, "message": f"Price data for {mid} is stale"})

        return {
            "summary": {
                "active": active,
                "deprecated": deprecated,
                "unavailable": unavailable,
                "drift_detected": drift_detected,
            },
            "alerts": alerts,
            "healthy": not any(a["severity"] in ("critical", "warning") for a in alerts),
        }

    def simulate_fable_deprecation(self, model_id: str = "gpt-4o", days_until_eol: int = 7) -> dict:
        """Simulate a sudden deprecation notice for a model.

        Useful for testing agent fallback logic and consolidation protocols.
        """
        now = datetime.now(timezone.utc)
        eol = now.replace(hour=0, minute=0, second=0, microsecond=0)
        eol_str = eol.replace(day=eol.day + days_until_eol).isoformat()

        if model_id not in self.models:
            self.register_or_update(model_id, provider="openai", status="active")

        rec = self.mark_deprecated(
            model_id,
            estimated_eol_at=eol_str,
            reason="Simulated Fable-style deprecation (test scenario)",
        )

        return {
            "simulated": True,
            "model_id": model_id,
            "estimated_eol_at": eol_str,
            "days_until_eol": days_until_eol,
            "record": {
                "model_id": rec.model_id,
                "provider": rec.provider,
                "status": rec.status,
                "notes": rec.notes[-3:],
            } if rec else None,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LAR Model Lifecycle CLI")
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path("model_lifecycle.json"),
        help="Path to lifecycle state JSON file",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Print lifecycle health report")

    register = sub.add_parser("register", help="Register or update a model")
    register.add_argument("model_id")
    register.add_argument("--provider", default="ollama")
    register.add_argument("--status", default="active")
    register.add_argument("--price-check", action="store_true")
    register.add_argument("--note", default=None)

    deprecate = sub.add_parser("deprecate", help="Mark a model as deprecated")
    deprecate.add_argument("model_id")
    deprecate.add_argument("--eol", default=None, help="Estimated EOL date (YYYY-MM-DD)")
    deprecate.add_argument("--reason", default="Provider deprecation notice")

    simulate = sub.add_parser("simulate-fable-deprecation", help="Simulate a sudden deprecation event")
    simulate.add_argument("--model-id", default="gpt-4o")
    simulate.add_argument("--days-until-eol", type=int, default=7)

    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    lifecycle = ModelLifecycle(state_path=args.state_path)

    if args.command == "check":
        report = lifecycle.check_health()
        print(json.dumps(report, indent=2))
    elif args.command == "register":
        rec = lifecycle.register_or_update(
            args.model_id,
            provider=args.provider,
            status=args.status,
            price_check=args.price_check,
            note=args.note,
        )
        print(json.dumps({
            "model_id": rec.model_id,
            "status": rec.status,
            "last_seen_at": rec.last_seen_at,
        }, indent=2))
    elif args.command == "deprecate":
        rec = lifecycle.mark_deprecated(args.model_id, args.eol, args.reason)
        if rec:
            print(json.dumps({
                "model_id": rec.model_id,
                "status": rec.status,
                "estimated_eol_at": rec.estimated_eol_at,
            }, indent=2))
        else:
            print(json.dumps({"error": f"Unknown model: {args.model_id}"}, indent=2))
    elif args.command == "simulate-fable-deprecation":
        result = lifecycle.simulate_fable_deprecation(args.model_id, args.days_until_eol)
        print(json.dumps(result, indent=2))


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()

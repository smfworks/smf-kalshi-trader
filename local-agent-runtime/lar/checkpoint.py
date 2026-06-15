"""
LAR — Checkpoint / Resume System

The defining capability of the Local Agent Runtime: durable agent state
at every step boundary. If the process crashes mid-workflow, resume
exactly where you left off.

Inspired by the Dawn Circle synthesis (June 10):
"Agents lose their place mid-workflow because state is in memory,
not persisted. The next frontier is checkpoint/resume at the step boundary."
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import aiosqlite


class Phase(str, Enum):
    """OATA loop phases that can be checkpointed."""
    OBSERVE = "observe"
    THINK = "think"
    ACT = "act"
    RESPOND = "respond"


@dataclass
class AgentState:
    """Complete serializable snapshot of agent execution state."""

    checkpoint_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    task_id: str = "default"
    step_number: int = 0
    phase: Phase = Phase.OBSERVE
    context: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, str]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    memory_snapshot: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    is_complete: bool = False
    error: str | None = None
    iteration: int = 0  # For multi-step tasks
    max_iterations: int = 10
    model_used: str = ""
    tools_available: list[str] = field(default_factory=list)
    checkpoint_reason: str = "step_boundary"  # step_boundary, crash, manual, circuit_breaker

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentState":
        # Convert string phase back to enum
        if isinstance(data.get("phase"), str):
            data["phase"] = Phase(data["phase"])
        return cls(**data)


class CheckpointStore:
    """SQLite-backed durable checkpoint store."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._initialized: bool = False

    async def _init(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_number INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    context_json TEXT,
                    messages_json TEXT,
                    tool_calls_json TEXT,
                    tool_results_json TEXT,
                    memory_snapshot_json TEXT,
                    timestamp TEXT NOT NULL,
                    is_complete INTEGER DEFAULT 0,
                    error TEXT,
                    iteration INTEGER DEFAULT 0,
                    max_iterations INTEGER DEFAULT 10,
                    model_used TEXT,
                    tools_available_json TEXT,
                    checkpoint_reason TEXT DEFAULT 'step_boundary',
                    FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(checkpoint_id)
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_phase
                ON checkpoints(task_id, phase)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_timestamp
                ON checkpoints(task_id, timestamp DESC)
                """
            )
            await db.commit()
        self._initialized = True

    async def save(self, state: AgentState) -> str:
        """Persist an agent state snapshot. Returns checkpoint_id."""
        await self._init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO checkpoints (
                    checkpoint_id, task_id, step_number, phase,
                    context_json, messages_json, tool_calls_json, tool_results_json,
                    memory_snapshot_json, timestamp, is_complete, error,
                    iteration, max_iterations, model_used, tools_available_json, checkpoint_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.checkpoint_id,
                    state.task_id,
                    state.step_number,
                    state.phase.value,
                    json.dumps(state.context),
                    json.dumps(state.messages),
                    json.dumps(state.tool_calls),
                    json.dumps(state.tool_results),
                    json.dumps(state.memory_snapshot),
                    state.timestamp,
                    int(state.is_complete),
                    state.error,
                    state.iteration,
                    state.max_iterations,
                    state.model_used,
                    json.dumps(state.tools_available),
                    state.checkpoint_reason,
                ),
            )
            await db.commit()
        return state.checkpoint_id

    async def load(self, checkpoint_id: str) -> AgentState | None:
        """Restore a checkpoint by ID."""
        await self._init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )
            row = await row.fetchone()
            if not row:
                return None
            return self._row_to_state(row)

    async def latest_for_task(self, task_id: str) -> AgentState | None:
        """Get the most recent checkpoint for a task."""
        await self._init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute(
                """
                SELECT * FROM checkpoints
                WHERE task_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (task_id,),
            )
            row = await row.fetchone()
            if not row:
                return None
            return self._row_to_state(row)

    async def list_for_task(
        self, task_id: str, limit: int = 50
    ) -> list[AgentState]:
        """List checkpoints for a task, newest first."""
        await self._init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute(
                """
                SELECT * FROM checkpoints
                WHERE task_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (task_id, limit),
            )
            rows = await rows.fetchall()
            return [self._row_to_state(r) for r in rows]

    async def incomplete_tasks(self) -> list[str]:
        """Return task_ids with at least one incomplete checkpoint."""
        await self._init()
        async with aiosqlite.connect(self.db_path) as db:
            rows = await db.execute(
                """
                SELECT DISTINCT task_id FROM checkpoints
                WHERE is_complete = 0
                ORDER BY timestamp DESC
                """
            )
            rows = await rows.fetchall()
            return [r[0] for r in rows]

    async def delete_old(self, days: int = 30) -> int:
        """Delete checkpoints older than N days. Returns count."""
        cutoff = datetime.utcnow().isoformat()
        await self._init()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM checkpoints
                WHERE timestamp < datetime('now', '-{} days')
                """.format(days)
            )
            await db.commit()
            return cursor.rowcount

    @staticmethod
    def _row_to_state(row: aiosqlite.Row) -> AgentState:
        """Convert DB row to AgentState."""
        return AgentState(
            checkpoint_id=row["checkpoint_id"],
            task_id=row["task_id"],
            step_number=row["step_number"],
            phase=Phase(row["phase"]),
            context=json.loads(row["context_json"] or "{}"),
            messages=json.loads(row["messages_json"] or "[]"),
            tool_calls=json.loads(row["tool_calls_json"] or "[]"),
            tool_results=json.loads(row["tool_results_json"] or "[]"),
            memory_snapshot=json.loads(row["memory_snapshot_json"] or "{}"),
            timestamp=row["timestamp"],
            is_complete=bool(row["is_complete"]),
            error=row["error"],
            iteration=row["iteration"],
            max_iterations=row["max_iterations"],
            model_used=row["model_used"] or "",
            tools_available=json.loads(row["tools_available_json"] or "[]"),
            checkpoint_reason=row["checkpoint_reason"] or "step_boundary",
        )

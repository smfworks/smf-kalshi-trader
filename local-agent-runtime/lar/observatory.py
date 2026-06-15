"""
LAR Observatory — Live Agent Visualizer

WebSocket server that streams agent state to a browser dashboard.
Renders: tool call graph, step latency, memory writes, circuit-breaker
state, checkpoint saves, error events.

Usage:
    lar observatory          # start server on localhost:8765
    lar observatory --port 9000
"""
from __future__ import annotations

import asyncio
import json
import time
import signal
import sys
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

try:
    import websockets
    from websockets.server import serve
except ImportError:
    websockets = None

try:
    from .health import HealthMonitor
    from .checkpoint import CheckpointStore
    from .circuit_breaker import CircuitBreaker
except ImportError:
    HealthMonitor = None
    CheckpointStore = None
    CircuitBreaker = None


@dataclass
class StepEvent:
    """One agent step (Observe/Think/Act/Respond)."""
    timestamp: float
    phase: str           # observe | think | act | respond | error
    step_number: int
    duration_ms: float
    detail: str = ""
    tool: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_output_preview: Optional[str] = None
    success: bool = True


@dataclass
class ObservatoryState:
    """Shared state, broadcast to all connected clients."""
    agent_id: str = "lar"
    started_at: float = field(default_factory=time.time)
    total_steps: int = 0
    current_phase: str = "idle"
    last_step_at: float = 0.0
    circuit_state: str = "CLOSED"
    health: dict = field(default_factory=dict)
    recent_steps: deque = field(default_factory=lambda: deque(maxlen=200))
    tool_counts: dict = field(default_factory=lambda: {
        "web_search": 0, "web_fetch": 0, "exec": 0,
        "file_read": 0, "file_write": 0, "other": 0,
    })
    error_count: int = 0
    checkpoint_count: int = 0
    memory_writes: int = 0
    uptime_s: float = 0.0

    def snapshot(self) -> dict:
        """Return JSON-serializable snapshot for clients."""
        self.uptime_s = time.time() - self.started_at
        return {
            "agent_id": self.agent_id,
            "uptime_s": round(self.uptime_s, 1),
            "total_steps": self.total_steps,
            "current_phase": self.current_phase,
            "last_step_at": self.last_step_at,
            "circuit_state": self.circuit_state,
            "health": self.health,
            "tool_counts": self.tool_counts,
            "error_count": self.error_count,
            "checkpoint_count": self.checkpoint_count,
            "memory_writes": self.memory_writes,
            "recent_steps": list(self.recent_steps),
        }


class Observatory:
    """WebSocket broadcaster + event logger."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765,
                 static_dir: Optional[Path] = None):
        self.host = host
        self.port = port
        self.state = ObservatoryState()
        self.clients: set = set()
        self.static_dir = static_dir or (Path(__file__).parent / "ui")
        self._lock = asyncio.Lock()
        self._running = False
        # Optional integrations
        self.health_monitor: Optional[HealthMonitor] = None
        self.checkpoint_store: Optional[CheckpointStore] = None
        self.circuit_breaker: Optional[CircuitBreaker] = None

    # ----- integration hooks --------------------------------------------

    def attach_health(self, monitor: "HealthMonitor") -> None:
        self.health_monitor = monitor
        self._refresh_health()

    def attach_checkpoints(self, store: "CheckpointStore") -> None:
        self.checkpoint_store = store
        if store:
            self.state.checkpoint_count = len(store.list_for_task("") or [])

    def attach_circuit_breaker(self, cb: "CircuitBreaker") -> None:
        self.circuit_breaker = cb
        if cb:
            self.state.circuit_state = cb.state.name

    def _refresh_health(self) -> None:
        if not self.health_monitor:
            return
        try:
            report = self.health_monitor.check_all()
            self.state.health = {
                "overall": report.overall.name,
                "checks": [
                    {"name": c.name, "status": c.status.name,
                     "latency_ms": c.latency_ms, "detail": c.detail}
                    for c in report.checks
                ],
            }
        except Exception as e:
            self.state.health = {"overall": "UNKNOWN", "error": str(e)}

    # ----- agent event API (called by Agent.run_cycle) -----------------

    def record_step(self, event: StepEvent) -> None:
        """Called after each OATA step."""
        self.state.total_steps += 1
        self.state.last_step_at = event.timestamp
        self.state.current_phase = "running"
        if not event.success or event.phase == "error":
            self.state.error_count += 1
        if event.tool:
            bucket = self.state.tool_counts.get(event.tool, self.state.tool_counts["other"])
            self.state.tool_counts[event.tool] = bucket + 1
        self.state.recent_steps.append({
            "timestamp": event.timestamp,
            "phase": event.phase,
            "step_number": event.step_number,
            "duration_ms": round(event.duration_ms, 1),
            "tool": event.tool,
            "detail": event.detail[:200],
            "success": event.success,
        })
        if self.circuit_breaker:
            self.state.circuit_state = self.circuit_breaker.state.name
        # schedule broadcast
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._broadcast_snapshot())
        except RuntimeError:
            pass  # no loop, will be picked up by periodic broadcast

    def record_checkpoint(self) -> None:
        self.state.checkpoint_count += 1

    def record_memory_write(self) -> None:
        self.state.memory_writes += 1

    def record_error(self, message: str) -> None:
        self.state.error_count += 1
        self.state.recent_steps.append({
            "timestamp": time.time(),
            "phase": "error",
            "step_number": -1,
            "duration_ms": 0,
            "tool": None,
            "detail": message[:200],
            "success": False,
        })

    # ----- websocket server ---------------------------------------------

    async def _broadcast_snapshot(self) -> None:
        if not self.clients:
            return
        self._refresh_health()
        msg = json.dumps(self.state.snapshot())
        dead = set()
        for ws in self.clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    async def _handler(self, ws) -> None:
        self.clients.add(ws)
        # send initial state immediately
        try:
            await ws.send(json.dumps(self.state.snapshot()))
            async for _ in ws:
                # client messages are pings; ignore
                pass
        finally:
            self.clients.discard(ws)

    async def _periodic(self, interval: float = 1.0) -> None:
        """Push a snapshot every interval even when idle."""
        while self._running:
            await asyncio.sleep(interval)
            if self.clients:
                await self._broadcast_snapshot()

    async def serve(self) -> None:
        if websockets is None:
            raise RuntimeError(
                "websockets not installed. Run: pip install websockets"
            )
        self._running = True
        async with serve(self._handler, self.host, self.port):
            print(f"[observatory] listening on ws://{self.host}:{self.port}")
            print(f"[observatory] dashboard: http://{self.host}:{self.port}/")
            await self._periodic()

    def run(self) -> None:
        try:
            asyncio.run(self.serve())
        except KeyboardInterrupt:
            print("\n[observatory] shutting down")


# ----- CLI entry --------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="lar observatory",
        description="Live agent visualizer (WebSocket + HTML)",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--agent-id", default="lar")
    args = p.parse_args(argv)

    obs = Observatory(host=args.host, port=args.port)
    obs.state.agent_id = args.agent_id
    obs.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

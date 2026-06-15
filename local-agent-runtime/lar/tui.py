"""
LAR TUI — Terminal-native attachment to a running agent

Usage:
    lar tui                    # attach to local observatory on default ports
    lar tui --port 8766        # custom WS port
    lar tui --demo             # synthetic event stream (no agent needed)

A Textual-based interface showing:
  - Real-time step stream (newest at top)
  - Tool call histogram
  - Step latency sparkline
  - Circuit-breaker state pill
  - Agent stats panel
  - Keyboard shortcuts (q to quit, c to clear, p to pause)

Press '?' for help.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import deque
from datetime import datetime

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Header, Footer, Static, ListView, ListItem, Label,
    Sparkline, ProgressBar, RichLog, Rule,
)

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False


# ── CSS for the TUI ──────────────────────────────────────────────────────

CSS = """
Screen {
    background: #0b0d12;
}

Header {
    background: #14181f;
    color: #d8dde6;
    text-style: bold;
}

Footer {
    background: #14181f;
    color: #7a8597;
}

#main {
    layout: horizontal;
    height: 1fr;
}

#left {
    width: 60%;
    border-right: solid #232a36;
    padding: 1;
}

#right {
    width: 40%;
    padding: 1;
}

.panel-title {
    color: #7a8597;
    text-style: bold;
    height: 1;
    margin-bottom: 1;
}

.stat-row {
    height: 3;
    content-align: left middle;
}

.stat-label {
    color: #7a8597;
    width: 18;
}

.stat-value {
    color: #d8dde6;
    text-style: bold;
}

#step-log {
    height: 1fr;
    background: #0b0d12;
    border: solid #232a36;
}

.step-line {
    height: 1;
}

.step-line.observe { color: #60a5fa; }
.step-line.think   { color: #a78bfa; }
.step-line.act     { color: #fbbf24; }
.step-line.respond { color: #10b981; }
.step-line.error   { color: #ef4444; }

#circuit-closed { color: #10b981; background: #0b1f17; padding: 0 1; }
#circuit-half   { color: #f59e0b; background: #1f1607; padding: 0 1; }
#circuit-open   { color: #ef4444; background: #1f0707; padding: 0 1; }

#status-bar {
    dock: bottom;
    height: 1;
    background: #14181f;
    color: #7a8597;
    padding: 0 1;
}

#conn-status {
    dock: top;
    height: 1;
    background: #14181f;
    color: #7a8597;
    padding: 0 1;
}

#conn-status.connected { color: #10b981; }
#conn-status.disconnected { color: #ef4444; }

#latency-spark {
    height: 4;
    background: #14181f;
    margin: 1 0;
}

#tools-table {
    height: auto;
    margin: 1 0;
}

.tool-row {
    height: 1;
}

.tool-name {
    color: #7a8597;
    width: 16;
}

.tool-bar-bg {
    background: #1a1f29;
}

.tool-bar-fg {
    background: #4f46e5;
}

.tool-count {
    color: #d8dde6;
    text-align: right;
    width: 6;
}
"""


# ── TUI App ──────────────────────────────────────────────────────────────

class LARTui(App):
    """LAR TUI — attach to a running agent via WebSocket."""

    CSS = CSS

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("c", "clear_log", "Clear log"),
        Binding("p", "toggle_pause", "Pause/Resume"),
        Binding("r", "reconnect", "Reconnect"),
        Binding("?", "help", "Help"),
    ]

    TITLE = "LAR TUI"
    SUB_TITLE = "Local Agent Runtime — Live View"

    # Reactive state
    connected: reactive[bool] = reactive(False)
    paused: reactive[bool] = reactive(False)
    total_steps: reactive[int] = reactive(0)
    error_count: reactive[int] = reactive(0)
    circuit_state: reactive[str] = reactive("CLOSED")
    current_phase: reactive[str] = reactive("idle")
    last_step_at: reactive[float] = reactive(0.0)
    tool_counts: reactive[dict] = reactive(dict)
    latency_history: reactive[list] = reactive(list)

    def __init__(self, ws_url: str = "ws://127.0.0.1:8766/ws",
                 use_demo: bool = False, demo_ticks: float = 0.4):
        super().__init__()
        self.ws_url = ws_url
        self.use_demo = use_demo
        self.demo_ticks = demo_ticks
        self._ws = None
        self._ws_task = None
        self._demo_task = None
        self._stop = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("● connecting…", id="conn-status", classes="disconnected")

        with Container(id="main"):
            # Left: step stream
            with Vertical(id="left"):
                yield Static("STEP STREAM  (newest first, 'p' to pause)", classes="panel-title")
                yield RichLog(id="step-log", highlight=False, markup=False, wrap=False)

            # Right: stats + tools + latency
            with Vertical(id="right"):
                yield Static("AGENT STATE", classes="panel-title")
                with Vertical():
                    yield Static("", id="stats-panel")

                yield Static("LATENCY (last 60 steps)", classes="panel-title")
                yield Sparkline([0] * 60, id="latency-spark", summary_function=max)

                yield Static("TOOL USAGE", classes="panel-title")
                yield Static("", id="tools-panel")

        yield Static("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        """Start the WebSocket connection or demo stream."""
        self._step_log = self.query_one("#step-log", RichLog)
        self._conn_status = self.query_one("#conn-status", Static)
        self._stats_panel = self.query_one("#stats-panel", Static)
        self._tools_panel = self.query_one("#tools-panel", Static)
        self._status_bar = self.query_one("#status-bar", Static)
        self._latency_spark = self.query_one("#latency-spark", Sparkline)

        if self.use_demo:
            self._conn_status.update("● demo mode (synthetic events)")
            self._conn_status.set_class(True, "connected")
            self.connected = True
            self._demo_task = asyncio.create_task(self._demo_loop())
        else:
            self._ws_task = asyncio.create_task(self._ws_loop())

        # Periodic UI refresher
        self._refresh_task = self.set_interval(0.5, self._refresh_ui)

    async def on_unmount(self) -> None:
        self._stop = True
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._ws_task:
            self._ws_task.cancel()
        if self._demo_task:
            self._demo_task.cancel()

    # ----- WebSocket loop -----

    async def _ws_loop(self) -> None:
        if not HAS_WS:
            self._conn_status.update("✗ websockets module not installed")
            return
        backoff = 1.0
        while not self._stop:
            try:
                self._conn_status.update(f"● connecting to {self.ws_url}…")
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    self.connected = True
                    self._conn_status.update(f"● connected to {self.ws_url}")
                    self._conn_status.set_class(True, "connected")
                    backoff = 1.0
                    async for msg in ws:
                        if self._stop:
                            break
                        try:
                            self._apply_snapshot(json.loads(msg))
                        except Exception as e:
                            self._status_bar.update(f"parse error: {e}")
            except Exception as e:
                self.connected = False
                self._conn_status.update(f"✗ disconnected: {e} (retry in {backoff:.0f}s)")
                self._conn_status.set_class(False, "connected")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    # ----- Demo loop (synthetic) -----

    async def _demo_loop(self) -> None:
        """Generate synthetic OATA events for demo/screenshots."""
        import random
        TOOLS = ["web_search", "web_fetch", "exec", "file_read", "file_write"]
        PHRASES = {
            "observe": [
                "user: 'summarize the latest OpenClaw release notes'",
                "identity check: payload signature verified",
                "loaded 7 tools from registry",
                "memory: 142 prior interactions in short-term",
            ],
            "think": [
                "planning: search → fetch → summarize",
                "selecting tool: web_search with k=10",
                "decoding tool call schema from model output",
                "synthesizing 3 chunks into single response",
            ],
            "respond": [
                "wrote 1,240 token summary with code blocks",
                "saved 3 checkpoints, response ready",
                "streamed final answer to user",
            ],
        }
        rng = random.Random()
        step = 0
        tools = {"web_search": 0, "web_fetch": 0, "exec": 0,
                 "file_read": 0, "file_write": 0, "other": 0}
        recent = deque(maxlen=60)
        while not self._stop:
            step += 1
            phase = rng.choices(
                ["observe", "think", "act", "respond", "error"],
                weights=[10, 25, 45, 18, 2],
            )[0]
            duration_ms = rng.uniform(5, 280)
            tool = None
            detail = ""
            if phase == "act":
                tool = rng.choice(TOOLS)
                tools[tool] = tools.get(tool, 0) + 1
                detail = f"invoked {tool}"
            elif phase == "error":
                detail = rng.choice([
                    "tool 'rag_query' not found in registry",
                    "LLM timeout after 30s on first attempt",
                    "checkpoint store write failed: disk full",
                ])
            else:
                detail = rng.choice(PHRASES[phase])
            self._apply_snapshot({
                "agent_id": "lar-demo",
                "total_steps": step,
                "current_phase": "running",
                "circuit_state": "CLOSED",
                "error_count": sum(1 for _ in range(step) if phase == "error"),
                "tool_counts": dict(tools),
                "recent_steps": [
                    {
                        "timestamp": time.time(),
                        "phase": phase,
                        "step_number": step,
                        "duration_ms": duration_ms,
                        "tool": tool,
                        "detail": detail,
                        "success": phase != "error",
                    }
                ] + list(recent),
            })
            recent.appendleft({
                "timestamp": time.time(),
                "phase": phase,
                "step_number": step,
                "duration_ms": duration_ms,
                "tool": tool,
                "detail": detail,
                "success": phase != "error",
            })
            await asyncio.sleep(self.demo_ticks)

    # ----- Apply incoming snapshot to UI state -----

    def _apply_snapshot(self, snap: dict) -> None:
        if self.paused:
            return
        prev_steps = self.total_steps
        self.total_steps = snap.get("total_steps", 0)
        self.error_count = snap.get("error_count", 0)
        self.circuit_state = snap.get("circuit_state", "CLOSED")
        self.current_phase = snap.get("current_phase", "idle")
        self.last_step_at = snap.get("last_step_at", 0.0)
        self.tool_counts = snap.get("tool_counts", {})

        # Append new steps to the log
        recent = snap.get("recent_steps", [])
        for st in recent:
            # If this is a new step (not in our history), write it
            if st.get("step_number", 0) > prev_steps:
                self._write_step(st)
        # Update latency sparkline
        lats = [s.get("duration_ms", 0) for s in recent[-60:]]
        if lats:
            self._latency_spark.data = lats

    def _write_step(self, st: dict) -> None:
        ts = datetime.fromtimestamp(st.get("timestamp", time.time())).strftime("%H:%M:%S")
        phase = st.get("phase", "?")
        step_num = st.get("step_number", "?")
        duration = st.get("duration_ms", 0)
        tool = st.get("tool")
        detail = st.get("detail", "")
        success = st.get("success", True)
        marker = "✓" if success else "✗"
        prefix = f"[{phase:7s}] #{step_num:>3} {ts} {duration:>6.1f}ms {marker} "
        if tool:
            prefix += f"[{tool}] "
        line = prefix + detail
        try:
            self._step_log.write(line)
        except Exception:
            pass

    def _refresh_ui(self) -> None:
        """Update stats panel + tools panel."""
        if not hasattr(self, '_stats_panel'):
            return
        # Stats panel
        uptime = ""
        if self.last_step_at:
            secs = time.time() - self.last_step_at
            uptime = f"{secs:.1f}s ago"
        self._stats_panel.update(
            f"  Total steps:    [b]{self.total_steps}[/b]\n"
            f"  Errors:         [b]{self.error_count}[/b]\n"
            f"  Current phase:  [b]{self.current_phase}[/b]\n"
            f"  Last step:      [b]{uptime or '—'}[/b]\n"
            f"  Circuit:        [{self._circuit_color()}]{self.circuit_state}[/{self._circuit_color()}]"
        )
        # Tools panel
        tools = self.tool_counts or {}
        if tools:
            max_ct = max(tools.values()) if any(tools.values()) else 1
            lines = []
            for name, count in sorted(tools.items(), key=lambda x: -x[1]):
                if count == 0:
                    continue
                bar_w = int(count / max_ct * 20)
                bar = "█" * bar_w
                lines.append(f"  {name:12s}  {bar:<20s}  {count:>4d}")
            self._tools_panel.update("\n".join(lines) if lines else "  (no tool calls)")
        else:
            self._tools_panel.update("  (no tool calls)")

        # Status bar
        self._status_bar.update(
            f"  {'PAUSED' if self.paused else 'LIVE'}   "
            f"  q: quit  c: clear  p: pause  r: reconnect  ?: help"
        )

    def _circuit_color(self) -> str:
        s = self.circuit_state.lower()
        if s == "open":
            return "red"
        if s == "half_open":
            return "yellow"
        return "green"

    # ----- Actions -----

    def action_clear_log(self) -> None:
        self._step_log.clear()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self._status_bar.update(f"  {'PAUSED' if self.paused else 'LIVE'}")

    def action_reconnect(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
        self._ws_task = asyncio.create_task(self._ws_loop())

    def action_help(self) -> None:
        self._step_log.write("─" * 60)
        self._step_log.write("LAR TUI — Keyboard shortcuts:")
        self._step_log.write("  q   Quit")
        self._step_log.write("  c   Clear step log")
        self._step_log.write("  p   Pause / Resume")
        self._step_log.write("  r   Reconnect WebSocket")
        self._step_log.write("  ?   Show this help")
        self._step_log.write("─" * 60)


# ── CLI entry point ──────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lar tui",
                                description="Terminal UI for LAR agent observatory")
    p.add_argument("--url", default="ws://127.0.0.1:8766/ws",
                   help="WebSocket URL of running observatory")
    p.add_argument("--demo", action="store_true",
                   help="Run with synthetic event stream (no agent needed)")
    p.add_argument("--demo-tick", type=float, default=0.4,
                   help="Demo tick interval in seconds")
    args = p.parse_args(argv)

    app = LARTui(ws_url=args.url, use_demo=args.demo, demo_ticks=args.demo_tick)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

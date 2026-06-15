"""
LAR Observatory Demo — synthetic agent event stream

Usage:
  # In-process: run demo and broadcast
  python -m lar.observatory_demo --duration 60
"""
from __future__ import annotations

import argparse
import random
import time

from .observatory import Observatory, StepEvent


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


def populate(obs: Observatory, duration_s: int = 60, tick_s: float = 0.6,
             seed: int = 42, verbose: bool = True) -> None:
    """Push synthetic events into the given observatory."""
    rng = random.Random(seed)
    start = time.time()
    step = 0
    if verbose:
        print(f"[demo] streaming {duration_s}s of synthetic activity to {obs.state.agent_id}")

    while time.time() - start < duration_s:
        step += 1
        phase = rng.choices(
            ["observe", "think", "act", "respond", "error"],
            weights=[10, 25, 45, 18, 2],
        )[0]

        if phase == "act":
            tool = rng.choice(TOOLS)
            duration_ms = rng.uniform(20, 280)
            obs.record_step(StepEvent(
                timestamp=time.time(), phase="act", step_number=step,
                duration_ms=duration_ms, tool=tool,
                tool_input={"query": "OpenClaw 2026.6.6"} if tool == "web_search" else {},
                success=True,
                detail=f"invoked {tool}",
            ))
        elif phase == "error":
            obs.record_step(StepEvent(
                timestamp=time.time(), phase="error", step_number=step,
                duration_ms=rng.uniform(50, 500), success=False,
                detail=rng.choice([
                    "tool 'rag_query' not found in registry",
                    "LLM timeout after 30s on first attempt",
                    "checkpoint store write failed: disk full",
                ]),
            ))
        else:
            phrase = rng.choice([p for p in PHRASES[phase] if p])
            obs.record_step(StepEvent(
                timestamp=time.time(), phase=phase, step_number=step,
                duration_ms=rng.uniform(5, 60) if phase != "respond" else rng.uniform(150, 800),
                detail=phrase, success=True,
            ))
            if phase == "respond":
                obs.record_checkpoint()

        time.sleep(tick_s)

    if verbose:
        print(f"[demo] complete: {step} steps in {duration_s}s")


def run(duration_s: int = 60, tick_s: float = 0.6) -> None:
    obs = Observatory()
    populate(obs, duration_s, tick_s)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=int, default=60)
    p.add_argument("--tick", type=float, default=0.6)
    args = p.parse_args()
    run(duration_s=args.duration, tick_s=args.tick)

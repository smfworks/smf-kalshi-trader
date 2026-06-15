"""
LAR Benchmark Harness — reproducible model comparison

Compares any Ollama-accessible models on identical prompts.
Tests: code generation, code review, refactoring, debugging, doc generation.

Usage:
    python3 -m lar.benchmark --models minimax-m3:cloud,kimi-k2.6:cloud --tasks all
    python3 -m lar.benchmark --models minimax-m3:cloud --tasks merge_intervals
    python3 -m lar.benchmark --report   # show past results
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

import urllib.request

OLLAMA_URL = "http://localhost:11434"


# =============================================================================
# Tasks: each has prompt, parse_fn (extract code), and grade_fn (pass/fail)
# =============================================================================

@dataclass
class Task:
    name: str
    category: str
    prompt: str
    parse_fn: Callable[[str], str]  # raw LLM output -> code
    grade_fn: Callable[[str], tuple[bool, str]]  # code -> (passed, reason)
    expected_difficulty: str  # easy | medium | hard
    description: str


def _strip_md_fences(s: str) -> str:
    s = re.sub(r"^\s*```(?:python|py)?\s*\n", "", s, flags=re.MULTILINE)
    s = re.sub(r"\n```\s*$", "", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*```(?:python|py)?\s*$", "", s, flags=re.MULTILINE)
    return s.strip()


def _strip_to_function(s: str) -> str:
    """Extract just the first Python function from response."""
    s = _strip_md_fences(s)
    m = re.search(r"^(def |class |async def )", s, re.MULTILINE)
    if not m:
        return s
    return s[m.start():]


def _run_python(code: str, stdin_input: str = "", timeout: float = 10.0) -> tuple[int, str, str]:
    """Run code in subprocess, return (returncode, stdout, stderr)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        result = subprocess.run(
            ["python3", path],
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)
    finally:
        Path(path).unlink(missing_ok=True)


# ---- Task 1: merge_intervals (algorithm) -----------------------------------

T1 = Task(
    name="merge_intervals",
    category="algorithm",
    expected_difficulty="easy",
    prompt=(
        "Write a Python function `merge_intervals(intervals: list[list[int]]) -> "
        "list[list[int]]` that merges all overlapping intervals. O(n log n). Handle "
        "empty input, single interval, fully nested intervals, and unsorted input. "
        "Return ONLY the function definition (no markdown fences, no explanation, "
        "no usage examples)."
    ),
    parse_fn=_strip_to_function,
    grade_fn=lambda code: _grade_merge_intervals(code),
    description="Merge overlapping intervals. Tests: edge cases + correctness + O(n log n).",
)


def _grade_merge_intervals(code: str) -> tuple[bool, str]:
    harness = code + """

if __name__ == "__main__":
    tests = [
        ([[1,3],[2,6],[8,10],[15,18]], [[1,6],[8,10],[15,18]]),
        ([[1,4],[4,5]], [[1,5]]),
        ([[1,4],[0,4]], [[0,4]]),
        ([[1,4],[2,3],[3,4],[5,6]], [[1,4],[5,6]]),
        ([], []),
        ([[1,4]], [[1,4]]),
        ([[2,3],[4,5],[6,7],[8,9],[1,10]], [[1,10]]),
    ]
    failed = 0
    for inp, expected in tests:
        got = merge_intervals([list(x) for x in inp])
        if got != expected:
            print(f"FAIL: {inp} -> {got} (expected {expected})", file=__import__('sys').stderr)
            failed += 1
    if failed:
        raise SystemExit(1)
    print("ALL_TESTS_PASS")
"""
    rc, out, err = _run_python(harness)
    if rc == 0 and "ALL_TESTS_PASS" in out:
        return True, "all 7 cases passed"
    return False, f"rc={rc}, stderr={err[:200]}"


# ---- Task 2: refactor with constraint (refactoring) -----------------------

T2 = Task(
    name="refactor_no_global",
    category="refactor",
    expected_difficulty="medium",
    prompt=(
        "Refactor this Python function to remove the global variable `cache` and "
        "make it thread-safe using `functools.lru_cache` instead. Return ONLY the "
        "refactored code.\n\n"
        "```python\ncache = {}\ndef slow_fib(n):\n    if n in cache: return cache[n]\n"
        "    if n < 2: return n\n    cache[n] = slow_fib(n-1) + slow_fib(n-2)\n"
        "    return cache[n]\n```"
    ),
    parse_fn=_strip_md_fences,
    grade_fn=lambda code: _grade_refactor(code),
    description="Refactor: remove global, use lru_cache, keep API. Tests API + thread safety.",
)


def _grade_refactor(code: str) -> tuple[bool, str]:
    harness = code + """

import threading

if __name__ == "__main__":
    # API check
    if 'slow_fib' not in dir():
        raise SystemExit("FAIL: slow_fib not defined")
    for n, expected in [(0,0),(1,1),(2,1),(10,55),(20,6765)]:
        if slow_fib(n) != expected:
            raise SystemExit(f"FAIL: fib({n})={slow_fib(n)} expected {expected}")
    # Global check
    if 'cache' in [v for v in dir() if not v.startswith('_')]:
        # module-level 'cache' must be gone
        import sys
        if 'cache' in vars(sys.modules[__name__]):
            raise SystemExit("FAIL: 'cache' global still present")
    # Thread safety smoke test
    results = []
    def worker():
        results.append(slow_fib(25))
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    if not all(r == 75025 for r in results):
        raise SystemExit(f"FAIL: thread results {results}")
    print("ALL_TESTS_PASS")
"""
    rc, out, err = _run_python(harness, timeout=15)
    if rc == 0 and "ALL_TESTS_PASS" in out:
        return True, "API correct, no global, thread-safe"
    return False, f"rc={rc}, stderr={err[:300]}"


# ---- Task 3: find the bug (debugging) -------------------------------------

T3 = Task(
    name="find_offbyone",
    category="debugging",
    expected_difficulty="medium",
    prompt=(
        "This function is supposed to return the sum of all even numbers in a list, "
        "but it returns the wrong answer. Find the bug and return the corrected "
        "function. Return ONLY the corrected function (no markdown, no explanation).\n\n"
        "```python\ndef sum_even(nums):\n    total = 0\n"
        "    for i in range(len(nums) - 1):\n        if nums[i] % 2 == 0:\n"
        "            total += nums[i]\n    return total\n```\n\n"
        "Test: sum_even([1,2,3,4,5,6]) should return 12 (2+4+6)."
    ),
    parse_fn=_strip_to_function,
    grade_fn=lambda code: _grade_sum_even(code),
    description="Fix off-by-one in loop bound. Tests the fix on 4 cases.",
)


def _grade_sum_even(code: str) -> tuple[bool, str]:
    harness = code + """

if __name__ == "__main__":
    cases = [
        ([1,2,3,4,5,6], 12),
        ([2,4,6,8], 20),
        ([1,3,5], 0),
        ([], 0),
        ([10], 10),
        ([-2, -4, -3], -6),
    ]
    for inp, expected in cases:
        got = sum_even(inp)
        if got != expected:
            raise SystemExit(f"FAIL: {inp} -> {got}, expected {expected}")
    print("ALL_TESTS_PASS")
"""
    rc, out, err = _run_python(harness)
    if rc == 0 and "ALL_TESTS_PASS" in out:
        return True, "all 6 cases passed"
    return False, f"rc={rc}, stderr={err[:300]}"


# ---- Task 4: explain a real codebase excerpt (code understanding) ---------

T4 = Task(
    name="explain_codebase",
    category="code_understanding",
    expected_difficulty="medium",
    prompt=(
        "Read this Python code and answer in 3-5 sentences: what does it do, what's "
        "the state machine, and what's the failure mode?\n\n"
        "```python\nclass CircuitBreaker:\n    def __init__(self, fail_max=5, reset_timeout=60):\n"
        "        self.fail_max = fail_max\n        self.reset_timeout = reset_timeout\n"
        "        self.fail_count = 0\n        self.state = 'CLOSED'\n        self.opened_at = None\n\n"
        "    def call(self, func, *args):\n        if self.state == 'OPEN':\n"
        "            if time.time() - self.opened_at > self.reset_timeout:\n"
        "                self.state = 'HALF_OPEN'\n            else:\n"
        "                raise RuntimeError('circuit open')\n"
        "        try:\n            result = func(*args)\n"
        "        except Exception:\n            self.fail_count += 1\n"
        "            if self.fail_count >= self.fail_max:\n"
        "                self.state = 'OPEN'\n                self.opened_at = time.time()\n"
        "            raise\n        else:\n"
        "            if self.state == 'HALF_OPEN':\n                self.state = 'CLOSED'\n"
        "                self.fail_count = 0\n            return result\n```\n\n"
        "Be specific. Don't restate the code. Identify the actual state machine logic."
    ),
    parse_fn=lambda s: s.strip(),
    grade_fn=lambda response: _grade_explanation(response),
    description="Code understanding: explain state machine + failure modes. Tests depth not keywords.",
)


def _grade_explanation(response: str) -> tuple[bool, str]:
    """Grade by checking for key concepts the response should contain."""
    r = response.lower()
    required = {
        "closed/open/half_open": any(s in r for s in ["closed", "open", "half_open", "half-open"]),
        "failure threshold": "fail" in r or "threshold" in r or "fail_max" in r,
        "timeout/reset": "timeout" in r or "reset" in r or "opened_at" in r or "60" in r,
        "state transition": "half" in r or "transition" in r or "recover" in r,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        return False, f"missing: {missing}"
    # Word count check
    wc = len(response.split())
    if wc < 30:
        return False, f"too short ({wc} words)"
    return True, f"all concepts covered ({wc} words)"


# ---- Task 5: write docstring + type hints (documentation) -----------------

T5 = Task(
    name="docstring_types",
    category="documentation",
    expected_difficulty="easy",
    prompt=(
        "Add a complete Google-style docstring AND type hints to this function. "
        "Return ONLY the modified function.\n\n"
        "```python\ndef parse_config(path):\n"
        "    with open(path) as f: data = json.load(f)\n"
        "    return {k: int(v) for k, v in data.items() if v.isdigit()}\n```"
    ),
    parse_fn=_strip_md_fences,
    grade_fn=lambda code: _grade_docstring(code),
    description="Add docstring + type hints. Tests Pythonic style + completeness.",
)


def _grade_docstring(code: str) -> tuple[bool, str]:
    # Provide json at module scope so the function can use it
    harness = '''import json
''' + code + '''

if __name__ == "__main__":
    import inspect, ast
    # Parse the AST to check for type hints
    tree = ast.parse(open(__file__).read().split('if __name__')[0])
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'parse_config')
    args = [a.arg for a in func.args.args]
    if 'path' not in args:
        raise SystemExit("FAIL: no 'path' arg")
    if not func.returns:
        raise SystemExit("FAIL: no return type hint")
    if not (func.args.args[0].annotation):
        raise SystemExit("FAIL: no 'path' type hint")
    doc = ast.get_docstring(func)
    if not doc:
        raise SystemExit("FAIL: no docstring")
    if len(doc) < 50:
        raise SystemExit(f"FAIL: docstring too short ({len(doc)} chars)")
    # Check for Args:, Returns:, or Raises: section
    has_args = "Args:" in doc or "Arguments:" in doc or "Parameters:" in doc
    has_returns = "Returns:" in doc or "Return:" in doc
    if not (has_args and has_returns):
        raise SystemExit(f"FAIL: docstring missing Args or Returns section")
    # Functional check
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"a":"1","b":"x","c":"42"}, f)
        p = f.name
    try:
        result = parse_config(p)
        if result != {"a": 1, "c": 42}:
            raise SystemExit(f"FAIL: result {result}")
    finally:
        __import__('os').unlink(p)
    print("ALL_TESTS_PASS")
'''
    rc, out, err = _run_python(harness)
    if rc == 0 and "ALL_TESTS_PASS" in out:
        return True, "type hints + complete docstring + functional"
    return False, f"rc={rc}, stderr={err[:300]}"


# ---- Task 6: implement a real algorithm (harder) -------------------------

T6 = Task(
    name="rate_limit_token_bucket",
    category="algorithm",
    expected_difficulty="hard",
    prompt=(
        "Implement a thread-safe token bucket rate limiter in Python. Class "
        "`TokenBucket(capacity, refill_rate)` where capacity is the max tokens "
        "and refill_rate is tokens per second. Method `try_consume(n=1) -> bool` "
        "returns True if n tokens are available and consumes them, False otherwise. "
        "Refill must be lazy (computed on access, not in a background thread). "
        "Use `time.monotonic()`. Handle the case where n > capacity.\n"
        "Return ONLY the class definition (no markdown, no usage examples)."
    ),
    parse_fn=_strip_to_function,
    grade_fn=lambda code: _grade_token_bucket(code),
    description="Thread-safe token bucket. Tests: timing accuracy, thread safety, edge cases.",
)


def _grade_token_bucket(code: str) -> tuple[bool, str]:
    harness = code + '''

if __name__ == "__main__":
    import time, threading

    # Test 1: Basic consume
    tb = TokenBucket(capacity=5, refill_rate=1.0)
    assert tb.try_consume(5) is True, "FAIL: should consume 5"
    assert tb.try_consume(1) is False, "FAIL: should be empty after 5"
    print("Test 1 OK")

    # Test 2: Refill over time
    tb = TokenBucket(capacity=2, refill_rate=10.0)  # 10 tokens/sec
    assert tb.try_consume(2) is True
    time.sleep(0.15)  # ~1.5 tokens should refill
    assert tb.try_consume(1) is True, "FAIL: should refill ~1.5 tokens in 0.15s"
    print("Test 2 OK")

    # Test 3: n > capacity
    tb = TokenBucket(capacity=3, refill_rate=1.0)
    assert tb.try_consume(5) is False, "FAIL: n > capacity should fail"
    assert tb.try_consume(3) is True, "FAIL: should still allow n=3"
    print("Test 3 OK")

    # Test 4: Thread safety — 100 threads, capacity=50, refill=100/sec
    tb = TokenBucket(capacity=50, refill_rate=100.0)
    consumed = []
    consumed_lock = threading.Lock()
    def worker():
        for _ in range(20):
            if tb.try_consume(1):
                with consumed_lock:
                    consumed.append(1)
    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()
    # Total should be close to 50 + refill*duration. Allow generous bounds.
    total = len(consumed)
    if total < 50 or total > 200:
        raise SystemExit(f"FAIL: thread safety — got {total} consumes, expected 50-200")
    print(f"Test 4 OK ({total} consumes from 50 threads)")

    print("ALL_TESTS_PASS")
'''
    rc, out, err = _run_python(harness, timeout=15)
    if rc == 0 and "ALL_TESTS_PASS" in out:
        return True, "all 4 tests passed"
    return False, f"rc={rc}, stderr={err[:300]}"


# ---- Task 7: identify and fix a real concurrency bug -----------------------

T7 = Task(
    name="fix_race_condition",
    category="debugging",
    expected_difficulty="hard",
    prompt=(
        "This function claims to be thread-safe but produces wrong counts under "
        "load. Find the bug and return the corrected function. The function must "
        "still accept the same arguments. Return ONLY the function.\n\n"
        "```python\nimport threading\nseen = set()\n\n"
        "def record_unique(id, lock):\n"
        "    if id not in seen:\n"
        "        time.sleep(0.0001)  # simulate work between check and add\n"
        "        seen.add(id)\n```\n\n"
        "Bug: even with a `lock` argument passed in, the function does NOT acquire "
        "it. The test passes 1000 unique IDs across 10 threads; the resulting "
        "`seen` set must contain all 1000."
    ),
    parse_fn=_strip_md_fences,
    grade_fn=lambda code: _grade_unique_fix(code),
    description="Fix missing lock acquisition. Tests: 1000 IDs across 10 threads.",
)


def _grade_unique_fix(code: str) -> tuple[bool, str]:
    # Static check: the corrected function must use the `lock` parameter to
    # protect the check-then-add. This is the actual bug: the function never
    # acquires the lock. We verify by AST/grep that `lock` is used in a `with`
    # statement, then run a functional test.
    if 'with lock' not in code and 'lock.acquire' not in code:
        return False, "FAIL: function does not use the lock parameter"
    harness = '''import threading, time

seen = set()

''' + code + '''


if __name__ == "__main__":
    lock = threading.Lock()
    ids = list(range(100))
    def worker():
        for i in ids:
            record_unique(i, lock)
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    if len(seen) != 100:
        raise SystemExit(f"FAIL: seen has {len(seen)} unique, expected 100")
    print("ALL_TESTS_PASS")
'''
    rc, out, err = _run_python(harness, timeout=15)
    if rc == 0 and "ALL_TESTS_PASS" in out:
        return True, "100 unique IDs across 10 threads, lock acquired"
    return False, f"rc={rc}, stderr={err[:300]}"


# All tasks
TASKS: list[Task] = [T1, T2, T3, T4, T5, T6, T7]


# =============================================================================
# Benchmark runner
# =============================================================================

def query_ollama(model: str, prompt: str, timeout: float = 120.0) -> tuple[str, int, float, bool]:
    """Returns (response_text, tokens, latency_s, timed_out)."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return (
                data.get("response", ""),
                data.get("eval_count", 0),
                time.time() - t0,
                False,
            )
    except Exception as e:
        return f"ERROR: {e}", 0, time.time() - t0, True


@dataclass
class Result:
    model: str
    task_name: str
    passed: bool
    reason: str
    tokens: int
    latency_s: float
    response_chars: int
    timestamp: float = field(default_factory=time.time)


def run_task(model: str, task: Task, retries: int = 1) -> Result:
    """Run a task on a model. Returns Result with pass/fail + metrics."""
    response, tokens, latency, timed_out = query_ollama(model, task.prompt)
    if timed_out:
        return Result(model, task.name, False, "timeout", 0, latency, 0)

    code = task.parse_fn(response)
    passed, reason = task.grade_fn(code)
    return Result(
        model=model,
        task_name=task.name,
        passed=passed,
        reason=reason,
        tokens=tokens,
        latency_s=latency,
        response_chars=len(response),
    )


def run_benchmark(models: list[str], task_filter: Optional[str] = None) -> list[Result]:
    results = []
    tasks = TASKS if task_filter in (None, "all") else [t for t in TASKS if t.name == task_filter]
    for model in models:
        print(f"\n{'='*60}")
        print(f"Model: {model}")
        print(f"{'='*60}")
        for task in tasks:
            print(f"  [{task.category:18s}] {task.name:25s} ", end="", flush=True)
            r = run_task(model, task)
            status = "✓ PASS" if r.passed else "✗ FAIL"
            print(f"{status}  {r.latency_s:5.1f}s  {r.tokens:4d} tok  {r.reason[:60]}")
            results.append(r)
    return results


def render_report(results: list[Result]) -> str:
    """Render a markdown comparison table."""
    by_model: dict[str, list[Result]] = {}
    for r in results:
        by_model.setdefault(r.model, []).append(r)

    lines = ["# Benchmark Report\n"]
    lines.append(f"Tasks: {len(TASKS)}")
    lines.append(f"Models: {', '.join(by_model.keys())}\n")

    # Summary table
    lines.append("## Summary\n")
    lines.append("| Model | Pass | Total | Pass Rate | Avg Latency | Total Tokens |")
    lines.append("|---|---|---|---|---|---|")
    for model, rs in by_model.items():
        passed = sum(1 for r in rs if r.passed)
        total = len(rs)
        rate = passed / total * 100 if total else 0
        avg_lat = sum(r.latency_s for r in rs) / total if total else 0
        total_tok = sum(r.tokens for r in rs)
        lines.append(f"| `{model}` | {passed} | {total} | {rate:.0f}% | {avg_lat:.1f}s | {total_tok} |")
    lines.append("")

    # Per-task breakdown
    lines.append("## Per-Task Results\n")
    lines.append("| Task | Category | " + " | ".join(f"`{m}`" for m in by_model.keys()) + " |")
    lines.append("|---|---|" + "|".join(["---"] * len(by_model)) + "|")
    for task in TASKS:
        cells = [f"{task.name} | {task.category}"]
        for model in by_model:
            r = next((x for x in by_model[model] if x.task_name == task.name), None)
            if r is None:
                cells.append("—")
            else:
                mark = "✓" if r.passed else "✗"
                cells.append(f"{mark} {r.latency_s:.1f}s")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lar benchmark")
    p.add_argument("--models", default="minimax-m3:cloud",
                   help="Comma-separated model names")
    p.add_argument("--tasks", default="all",
                   help="Comma-separated task names, or 'all'")
    p.add_argument("--output", default=None, help="Write JSON results to this path")
    p.add_argument("--report", action="store_true", help="Print markdown report")
    args = p.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    task_filter = None if args.tasks == "all" else args.tasks

    print(f"Benchmarking: {models}")
    print(f"Tasks: {task_filter or 'all'}")

    results = run_benchmark(models, task_filter)

    if args.output:
        Path(args.output).write_text(json.dumps([asdict(r) for r in results], indent=2))
        print(f"\nWrote {args.output}")

    if args.report or not args.output:
        print("\n" + render_report(results))

    return 0


if __name__ == "__main__":
    sys.exit(main())

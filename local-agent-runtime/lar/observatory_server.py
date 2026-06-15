"""
LAR Observatory Serve — HTTP + WebSocket combined server

Serves:
  GET  /              → static dashboard.html
  GET  /index.html    → same
  GET  /ws            → live state stream (WebSocket)
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

try:
    import websockets
    from websockets.server import serve
except ImportError:
    websockets = None

from .observatory import Observatory


UI_DIR = Path(__file__).parent / "ui"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            target = UI_DIR / "dashboard.html"
        elif p.startswith("/static/"):
            target = (UI_DIR / p[len("/static/"):]).resolve()
            if not str(target).startswith(str(UI_DIR.resolve())):
                self.send_error(403); return
        else:
            target = (UI_DIR / p.lstrip("/")).resolve()
            if not str(target).startswith(str(UI_DIR.resolve())):
                self.send_error(403); return
        if not target.is_file():
            self.send_error(404); return
        ctype = "text/html" if target.suffix == ".html" else "application/octet-stream"
        try:
            data = target.read_bytes()
        except Exception as e:
            self.send_error(500, str(e)); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass  # quiet


def _start_http(port: int) -> HTTPServer:
    httpd = HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True, name="lar-http")
    t.start()
    return httpd


async def _run(observatory: Observatory, ws_port: int) -> None:
    if websockets is None:
        raise RuntimeError("websockets not installed. Run: pip install websockets")

    async def handler(ws):
        observatory.clients.add(ws)
        try:
            await ws.send(json.dumps(observatory.state.snapshot()))
            async for _ in ws:
                pass
        finally:
            observatory.clients.discard(ws)

    async def periodic():
        while True:
            await asyncio.sleep(1.0)
            if observatory.clients:
                msg = json.dumps(observatory.state.snapshot())
                dead = set()
                for ws in observatory.clients:
                    try:
                        await ws.send(msg)
                    except Exception:
                        dead.add(ws)
                observatory.clients -= dead

    observatory._running = True
    async with serve(handler, observatory.host, ws_port):
        asyncio.create_task(periodic())
        await asyncio.Future()  # run forever


def serve_combined(host: str = "127.0.0.1", http_port: int = 8765,
                   ws_port: int = 8766,
                   observatory: Observatory | None = None) -> None:
    obs = observatory or Observatory(host=host, port=ws_port)
    httpd = _start_http(http_port)
    print(f"[observatory] HTTP → http://{host}:{http_port}/")
    print(f"[observatory] WS   → ws://{host}:{ws_port}/ws")
    print(f"[observatory] dashboard ready at http://{host}:{http_port}/")
    try:
        asyncio.run(_run(obs, ws_port))
    except KeyboardInterrupt:
        print("\n[observatory] shutting down")
    finally:
        httpd.shutdown()


def serve_with_demo(host: str = "127.0.0.1", http_port: int = 8765,
                    ws_port: int = 8766, duration_s: int = 60,
                    tick_s: float = 0.5) -> None:
    """Server + synthetic demo running against the SAME observatory instance.

    For blog demos, screenshots, presentations.
    """
    import threading
    from .observatory_demo import populate
    obs = Observatory(host=host, port=ws_port)
    t = threading.Thread(
        target=populate, kwargs={"obs": obs, "duration_s": duration_s,
                                 "tick_s": tick_s, "verbose": True},
        daemon=True,
    )
    t.start()
    serve_combined(host=host, http_port=http_port, ws_port=ws_port, observatory=obs)


if __name__ == "__main__":
    serve_combined()

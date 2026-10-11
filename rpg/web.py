"""The web viewer: a FastAPI app over a Host. Needs
`pip install -r requirements-web.txt` (only for --web; the terminal game stays
stdlib-only).

    GET  /                 the viewer page (web/index.html)
    GET  /api/events?since=N   output events from N on
    POST /api/input        {"text": "...", "source": "cli", "wait": true}
                           queue a line; with wait, block until it has been
                           played and return the events it produced
    WS   /ws?since=N       events from N on, then live
"""
import asyncio
import threading
from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web"


def make_app(host):
    from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import FileResponse

    app = FastAPI(title="AI RPG")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/events")
    def events(since: int = 0):
        with host.lock:
            return host.events[since:]

    @app.post("/api/input")
    async def send(text: str = Body(...), source: str = Body("web"), wait: bool = Body(False)):
        job = host.submit(text, source)
        if not wait:
            return {"queued": True}
        await run_in_threadpool(job.done.wait)
        with host.lock:
            return {"events": host.events[job.first:job.last]}

    @app.websocket("/ws")
    async def ws(socket: WebSocket, since: int = 0):
        await socket.accept()
        loop, inbox = asyncio.get_running_loop(), asyncio.Queue()

        def listener(ev):
            loop.call_soon_threadsafe(inbox.put_nowait, ev)

        backlog = host.subscribe(listener, since)
        try:
            if backlog:
                await socket.send_json({"backlog": backlog})
            while True:
                await socket.send_json(await inbox.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            host.unsubscribe(listener)

    return app


def serve(host, address="127.0.0.1", port=8000):
    """Start the web server in a background thread."""
    try:
        import uvicorn
        import websockets  # noqa: F401  uvicorn serves no WebSockets without it
        app = make_app(host)
    except ImportError:
        raise SystemExit("--web needs FastAPI: pip install -r requirements-web.txt")
    server = uvicorn.Server(uvicorn.Config(app, host=address, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return server

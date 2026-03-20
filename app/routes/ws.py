"""
ws.py — WebSocket handler for Rythmx (SHRTA-compliant).

Responsibilities:
  - Maintain a registry of connected clients
  - Provide broadcast() for server-push events from background threads
  - Enforce Origin-based access control on every connection (SHRTA Section 3)
  - Reject unregistered event names with protocol_error (SHRTA Section 6)

All messages use the standard SHRTA envelope:
    { "event": str, "payload": dict, "timestamp": int (Unix seconds) }

Approved events (from SHRTA Section 6 registry):
    server → client: pipeline_progress, pipeline_complete, pipeline_error,
                     enrichment_progress, enrichment_complete, enrichment_stopped,
                     library_sync_progress, player_state, ping, protocol_error
    client → server: pong

Production deployment note (SHRTA Section 4):
    Traefik: add readTimeout middleware + websocket: true on the router.
    Nginx: proxy_read_timeout 3600s; proxy_send_timeout 3600s.
"""
import asyncio
import json
import logging
import threading
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import config

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Client registry — thread-safe set of active WS connections
# ---------------------------------------------------------------------------

_clients: set[WebSocket] = set()
_clients_lock = threading.Lock()

# Stored at lifespan startup so background threads can schedule async sends.
_event_loop: asyncio.AbstractEventLoop | None = None

# Events the server is allowed to emit (SHRTA Section 6)
_SERVER_EVENTS = frozenset({
    "pipeline_progress", "pipeline_complete", "pipeline_error",
    "enrichment_progress", "enrichment_complete", "enrichment_stopped",
    "library_sync_progress", "player_state",
    "ping", "protocol_error",
})

# Events the client is allowed to send
_CLIENT_EVENTS = frozenset({"pong"})


# ---------------------------------------------------------------------------
# Called from main.py lifespan to bind the running event loop
# ---------------------------------------------------------------------------

def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _event_loop
    _event_loop = loop


# ---------------------------------------------------------------------------
# Public API — call from background threads (enrichment workers, scheduler)
# ---------------------------------------------------------------------------

def broadcast(event: str, payload: dict) -> None:
    """
    Push a SHRTA-framed event to all connected WebSocket clients.

    Thread-safe. Dead connections are pruned automatically.
    No-op if no clients are connected or event is unregistered.
    """
    if event not in _SERVER_EVENTS:
        logger.warning("ws.broadcast: unregistered event '%s' — dropped", event)
        return

    if _event_loop is None or not _clients:
        return

    message = json.dumps({
        "event": event,
        "payload": payload,
        "timestamp": int(time.time()),
    })

    # Snapshot clients under lock, then send outside the lock to avoid deadlocks.
    with _clients_lock:
        clients_snapshot = list(_clients)

    dead: set[WebSocket] = set()
    for ws in clients_snapshot:
        try:
            fut = asyncio.run_coroutine_threadsafe(ws.send_text(message), _event_loop)
            fut.result(timeout=5)
        except TimeoutError:
            # Event loop busy — do NOT prune. Client is likely still alive.
            logger.debug("ws.broadcast: send to client timed out (kept alive)")
        except Exception:
            dead.add(ws)

    if dead:
        with _clients_lock:
            _clients -= dead
        logger.info("ws.broadcast: pruned %d dead connection(s)", len(dead))


# ---------------------------------------------------------------------------
# Heartbeat — server-side ping to keep proxy connections alive (SHRTA Section 4)
# ---------------------------------------------------------------------------

def _start_heartbeat(interval: int = 15) -> None:
    """
    Launch a daemon thread that sends ping to all clients every `interval` seconds.
    Prevents Traefik / Nginx proxy idle-connection drops on enrichment runs.
    Dead connections are pruned automatically by broadcast().
    """
    def _loop() -> None:
        while True:
            time.sleep(interval)
            if _clients:
                broadcast("ping", {})

    t = threading.Thread(target=_loop, daemon=True, name="ws-heartbeat")
    t.start()
    logger.info("ws: heartbeat started (interval=%ds)", interval)


# ---------------------------------------------------------------------------
# WebSocket connection handler
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def ws_handler(websocket: WebSocket) -> None:
    """
    Single WebSocket endpoint. Registered via app.include_router() in main.py.

    On connect:
      1. Origin validation (SHRTA Section 3) — reject unknown origins immediately.
      2. Accept the connection.
      3. Add to client registry.

    Loop:
      - Receive messages from client (currently only 'pong' is valid).
      - Timeout every 20s to keep the loop alive without blocking forever.
      - On WebSocketDisconnect or any fatal error: fall through to finally.

    On disconnect: remove from registry.
    """
    # --- Origin check (SHRTA Section 3) ---
    # Empty allowed_origins list = allow any origin (default for self-hosted LAN deployments).
    # Set WS_ALLOWED_ORIGINS env var to restrict (e.g. "mysite.example.com").
    origin = websocket.headers.get("origin", "")
    allowed_origins: list[str] = config.WS_ALLOWED_ORIGINS
    if allowed_origins and origin and not any(o in origin for o in allowed_origins):
        logger.warning("ws: rejected connection from disallowed origin '%s'", origin)
        await websocket.close(1008)
        return

    await websocket.accept()

    with _clients_lock:
        _clients.add(websocket)
    logger.debug(
        "ws: client connected (origin=%s, total=%d)",
        origin or "unknown",
        len(_clients),
    )

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=20)
            except asyncio.TimeoutError:
                # Client still connected, just quiet — loop continues
                continue
            except WebSocketDisconnect:
                break

            try:
                msg = json.loads(data)
            except (ValueError, TypeError):
                await websocket.send_text(json.dumps({
                    "event": "protocol_error",
                    "payload": {"message": "Invalid JSON"},
                    "timestamp": int(time.time()),
                }))
                continue

            event = msg.get("event", "")
            if event not in _CLIENT_EVENTS:
                await websocket.send_text(json.dumps({
                    "event": "protocol_error",
                    "payload": {"message": f"Unknown event: {event}"},
                    "timestamp": int(time.time()),
                }))
    except Exception:
        pass
    finally:
        with _clients_lock:
            _clients.discard(websocket)
        logger.debug("ws: client disconnected (remaining=%d)", len(_clients))

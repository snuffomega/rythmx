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
import json
import logging
import threading
import time

from flask import current_app
from flask_sock import Sock

logger = logging.getLogger(__name__)

sock = Sock()

# ---------------------------------------------------------------------------
# Client registry — thread-safe set of active WS connections
# ---------------------------------------------------------------------------

_clients: set = set()
_clients_lock = threading.Lock()

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

    message = json.dumps({
        "event": event,
        "payload": payload,
        "timestamp": int(time.time()),
    })

    dead: set = set()
    with _clients_lock:
        for ws in _clients:
            try:
                ws.send(message)
            except Exception:
                dead.add(ws)
        _clients -= dead

    if dead:
        logger.debug("ws.broadcast: pruned %d dead connection(s)", len(dead))


# ---------------------------------------------------------------------------
# WebSocket connection handler
# ---------------------------------------------------------------------------

@sock.route("/ws")
def ws_handler(ws) -> None:
    """
    Single WebSocket endpoint. Registered via sock.init_app(app) in main.py.

    On connect:
      1. Origin validation (SHRTA Section 3) — reject unknown origins immediately.
      2. Add to client registry.

    Loop:
      - Receive messages from client (currently only 'pong' is valid).
      - Timeout every 20s to keep the loop alive without blocking forever.
      - On ConnectionClosed or any fatal error: fall through to finally.

    On disconnect: remove from registry.
    """
    # --- Origin check (SHRTA Section 3) ---
    # Empty allowed_origins list = allow any origin (default for self-hosted LAN deployments).
    # Set WS_ALLOWED_ORIGINS env var to restrict (e.g. "mysite.example.com").
    origin = ws.environ.get("HTTP_ORIGIN", "")
    allowed_origins: list[str] = current_app.config.get("WS_ALLOWED_ORIGINS", [])
    if allowed_origins and origin and not any(o in origin for o in allowed_origins):
        logger.warning("ws: rejected connection from disallowed origin '%s'", origin)
        ws.close(1008, "Origin not allowed")
        return

    with _clients_lock:
        _clients.add(ws)
    logger.debug("ws: client connected (origin=%s, total=%d)", origin or "unknown", len(_clients))

    try:
        while True:
            try:
                data = ws.receive(timeout=20)
            except Exception:
                # ConnectionClosed or transport error — exit cleanly
                break

            if data is None:
                # Timeout — client still connected, just quiet; loop continues
                continue

            try:
                msg = json.loads(data)
            except (ValueError, TypeError):
                ws.send(json.dumps({
                    "event": "protocol_error",
                    "payload": {"message": "Invalid JSON"},
                    "timestamp": int(time.time()),
                }))
                continue

            event = msg.get("event", "")
            if event not in _CLIENT_EVENTS:
                ws.send(json.dumps({
                    "event": "protocol_error",
                    "payload": {"message": f"Unknown event: {event}"},
                    "timestamp": int(time.time()),
                }))
    except Exception:
        pass
    finally:
        with _clients_lock:
            _clients.discard(ws)
        logger.debug("ws: client disconnected (remaining=%d)", len(_clients))

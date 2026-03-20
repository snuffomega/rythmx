/**
 * wsService.ts — WebSocket singleton for the Rythmx app.
 *
 * One connection for the entire browser session, outside React lifecycle.
 * Events are routed directly to Zustand store action methods.
 *
 * Adding a new event type:
 *   1. Add an entry to ROUTES pointing at the relevant store action.
 *   2. Add the action to the store.
 *   That's it — no hooks, no components, no other changes.
 *
 * Usage (App.tsx):
 *   useEffect(() => { wsConnect(); return () => wsDisconnect(); }, []);
 */
import { useEnrichmentStore } from '../stores/useEnrichmentStore';
import { enrichmentApi } from './api';
// import { usePlayerStore } from '../stores/usePlayerStore';  // ← Phase 22+

const WS_INITIAL_DELAY = 3000;
const WS_MAX_DELAY = 30000;

let _ws: WebSocket | null = null;
let _retryDelay = WS_INITIAL_DELAY;
let _stopped = false;

// ---------------------------------------------------------------------------
// Event routing table — one line per server event type
// ---------------------------------------------------------------------------

const ROUTES: Record<string, (payload: unknown) => void> = {
  enrichment_progress: (p) => useEnrichmentStore.getState().handleProgress(p),
  enrichment_complete: (p) => useEnrichmentStore.getState().handleComplete(p),
  enrichment_stopped:  ()  => useEnrichmentStore.getState().handleStopped(),
  // player_state: (p) => usePlayerStore.getState().handleState(p),
};

// ---------------------------------------------------------------------------
// Internal connection logic
// ---------------------------------------------------------------------------

function connect(): void {
  if (_stopped) return;

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
  _ws = ws;

  ws.onopen = () => {
    _retryDelay = WS_INITIAL_DELAY;
    // Reseed store on every (re)connect — catches any events missed during disconnect.
    enrichmentApi.status()
      .then(useEnrichmentStore.getState().setFromStatus)
      .catch(() => {});
  };

  ws.onmessage = (e: MessageEvent) => {
    try {
      const { event, payload } = JSON.parse(e.data) as { event: string; payload: unknown };
      if (event === 'ping') {
        ws.send(JSON.stringify({ event: 'pong' }));
        return;
      }
      ROUTES[event]?.(payload);
    } catch {
      // malformed message — ignore
    }
  };

  ws.onclose = () => {
    if (_stopped) return;
    setTimeout(connect, _retryDelay);
    _retryDelay = Math.min(_retryDelay * 1.5, WS_MAX_DELAY);
  };

  ws.onerror = () => {
    ws.close(); // onclose handles reconnect
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Connect the singleton WebSocket. Call once on app mount. */
export function wsConnect(): void {
  _stopped = false;
  connect();
}

/** Disconnect and suppress reconnection. Call on app unmount (cleanup). */
export function wsDisconnect(): void {
  _stopped = true;
  _ws?.close();
}

/**
 * useWebSocket.ts — SHRTA-compliant WebSocket client hook.
 *
 * Connects to /ws on mount. Calls onMessage for every server-pushed event.
 * Reconnects automatically after disconnect (3s initial, exponential backoff up to 30s).
 *
 * Usage:
 *   useWebSocket((event, payload) => {
 *     if (event === 'enrichment_progress') { ... }
 *   });
 *
 * The /ws endpoint does not require the X-Api-Key header — origin validation
 * is the security gate (SHRTA Section 3).
 */
import { useEffect, useRef, useCallback } from 'react';
import type { WsEnvelope } from '../types';

type MessageHandler = (event: string, payload: unknown) => void;

export function useWebSocket(onMessage: MessageHandler): void {
  const wsRef = useRef<WebSocket | null>(null);
  const retryDelayRef = useRef<number>(3000);
  const unmountedRef = useRef<boolean>(false);
  const handlerRef = useRef<MessageHandler>(onMessage);

  // Keep handler ref current without re-triggering the effect
  handlerRef.current = onMessage;

  const connect = useCallback(() => {
    if (unmountedRef.current) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      retryDelayRef.current = 3000; // reset backoff on successful connect
    };

    ws.onmessage = (e: MessageEvent) => {
      try {
        const envelope = JSON.parse(e.data) as WsEnvelope;
        if (envelope.event === 'ping') {
          // Internal heartbeat — respond with pong, never forward to onMessage
          ws.send(JSON.stringify({ event: 'pong' }));
          return;
        }
        handlerRef.current(envelope.event, envelope.payload);
      } catch {
        // malformed message — ignore
      }
    };

    ws.onclose = () => {
      if (unmountedRef.current) return;
      const delay = retryDelayRef.current;
      retryDelayRef.current = Math.min(delay * 1.5, 30000); // exponential backoff, cap 30s
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      // onerror always fires before onclose — let onclose handle reconnect
      ws.close();
    };
  }, []);

  useEffect(() => {
    unmountedRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      wsRef.current?.close();
    };
  }, [connect]);
}

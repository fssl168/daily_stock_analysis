/**
 * Generic WebSocket hook with auto-reconnect and heartbeat.
 *
 * Uses the same shared-singleton pattern as useTaskStream so multiple
 * components subscribing to the same WS endpoint share one connection.
 *
 * Usage:
 *   const { isConnected, lastMessage } = useWebSocket({
 *     url: '/api/v1/paper-trading/ws/quotes',
 *     enabled: true,
 *   });
 */

import { useEffect, useRef, useState, useCallback } from 'react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseWebSocketOptions {
  /** Full WebSocket URL (e.g. 'wss://host/api/v1/ws/quotes'). */
  url: string;
  /** Whether the hook should connect (default true). */
  enabled?: boolean;
  /** Auto-reconnect after close (default true). */
  autoReconnect?: boolean;
  /** Initial reconnect delay in ms (default 1000). */
  reconnectDelay?: number;
  /** Max reconnect delay in ms (default 30000). */
  maxReconnectDelay?: number;
  /** Called when the connection opens. */
  onOpen?: (event: Event) => void;
  /** Called when the connection closes. */
  onClose?: (event: CloseEvent) => void;
  /** Called on error. */
  onError?: (event: Event) => void;
}

export interface UseWebSocketResult<T = unknown> {
  /** Whether the WebSocket is currently open. */
  isConnected: boolean;
  /** The most recent parsed message, or null. */
  lastMessage: T | null;
  /** Send a JSON-serialisable message. */
  send: (data: unknown) => void;
  /** Force disconnect (auto-reconnect stops). */
  disconnect: () => void;
  /** Force reconnect now. */
  reconnect: () => void;
}

// ---------------------------------------------------------------------------
// Shared singleton (one WS per URL, ref-counted)
// ---------------------------------------------------------------------------

interface WsSubscriber {
  /** Store callbacks in a mutable ref so updates never create stale closures. */
  cbRef: {
    current: {
      onOpen?: (e: Event) => void;
      onClose?: (e: CloseEvent) => void;
      onError?: (e: Event) => void;
      onMessage: (data: unknown) => void;
    };
  };
  setIsConnected: (v: boolean) => void;
  autoReconnect: boolean;
  initialDelay: number;
  maxDelay: number;
}

interface WsState {
  socket: WebSocket | null;
  connected: boolean;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  reconnectDelay: number;
  subscribers: Set<WsSubscriber>;
}

const wsRegistry = new Map<string, WsState>();

/** Resolve a relative URL against the current origin. */
function resolveWsUrl(url: string): string {
  if (url.startsWith('ws')) return url;
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${url}`;
}

/** Notify every subscriber of the current connection state. */
function notifyConnected(state: WsState, connected: boolean): void {
  state.connected = connected;
  state.subscribers.forEach((sub) => sub.setIsConnected(connected));
}

function clearReconnect(state: WsState): void {
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
}

/** Close the shared socket and remove from registry (all subscribers gone). */
function teardownShared(url: string, state: WsState): void {
  clearReconnect(state);
  if (state.socket) {
    // Remove handlers before close so onclose doesn't schedule reconnect.
    state.socket.onopen = null;
    state.socket.onmessage = null;
    state.socket.onclose = null;
    state.socket.onerror = null;
    try {
      state.socket.close();
    } catch {
      // ignore
    }
    state.socket = null;
  }
  wsRegistry.delete(url);
  notifyConnected(state, false);
}

function scheduleReconnect(url: string, state: WsState, sub: WsSubscriber): void {
  if (state.reconnectTimer) return;
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    // Only reconnect if there are still subscribers who want auto-reconnect.
    const anyWantReconnect = Array.from(state.subscribers).some((s) => s.autoReconnect);
    if (anyWantReconnect && state.subscribers.size > 0) {
      connectShared(url);
    } else if (state.subscribers.size === 0) {
      teardownShared(url, state);
    }
  }, state.reconnectDelay);
  state.reconnectDelay = Math.min(state.reconnectDelay * 2, sub.maxDelay);
}

function connectShared(url: string): void {
  let state = wsRegistry.get(url);
  if (state?.socket && state.socket.readyState === WebSocket.OPEN) {
    notifyConnected(state, true);
    return;
  }
  if (state?.socket && state.socket.readyState === WebSocket.CONNECTING) {
    return; // already in-flight
  }

  if (!state) {
    state = {
      socket: null,
      connected: false,
      reconnectTimer: null,
      reconnectDelay: 1000,
      subscribers: new Set(),
    };
    wsRegistry.set(url, state);
  }

  const socket = new WebSocket(url);

  socket.onopen = () => {
    state!.reconnectDelay = 1000;
    notifyConnected(state!, true);
    state!.subscribers.forEach((s) => s.cbRef.current.onOpen?.(new Event('open')));
  };

  socket.onmessage = (event) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(event.data as string);
    } catch {
      return; // ignore non-JSON
    }
    state!.subscribers.forEach((s) => s.cbRef.current.onMessage(parsed));
  };

  socket.onclose = (event) => {
    notifyConnected(state!, false);
    state!.subscribers.forEach((s) => s.cbRef.current.onClose?.(event));
    state!.socket = null;
    // Schedule reconnect if anyone wants it.
    const anyWantReconnect = Array.from(state!.subscribers).some((s) => s.autoReconnect);
    if (anyWantReconnect && state!.subscribers.size > 0) {
      const firstSub = Array.from(state!.subscribers)[0];
      scheduleReconnect(url, state!, firstSub);
    } else if (state!.subscribers.size === 0) {
      teardownShared(url, state!);
    }
  };

  socket.onerror = (event) => {
    state!.subscribers.forEach((s) => s.cbRef.current.onError?.(event));
    // onclose will fire next; close explicitly to trigger it.
    if (socket.readyState !== WebSocket.CLOSED) {
      try {
        socket.close();
      } catch {
        // ignore
      }
    }
  };

  state.socket = socket;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useWebSocket<T = unknown>(
  options: UseWebSocketOptions,
): UseWebSocketResult<T> {
  const {
    url,
    enabled = true,
    autoReconnect = true,
    reconnectDelay: initialDelay = 1000,
    maxReconnectDelay = 30000,
    onOpen,
    onClose,
    onError,
  } = options;

  const resolvedUrl = resolveWsUrl(url);

  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<T | null>(null);

  // Mutable ref so the shared singleton always calls the LATEST handlers.
  // Sync after every render (not during render) to satisfy React ref rules.
  const cbRef = useRef({
    onOpen,
    onClose,
    onError,
    onMessage: (data: unknown) => setLastMessage(data as T),
  });
  useEffect(() => {
    cbRef.current = {
      onOpen,
      onClose,
      onError,
      onMessage: (data: unknown) => setLastMessage(data as T),
    };
  });

  const subscriberRef = useRef<WsSubscriber | null>(null);

  // ---- Lifecycle: subscribe / unsubscribe ----
  useEffect(() => {
    if (!enabled) return;

    let state = wsRegistry.get(resolvedUrl);
    if (!state) {
      state = {
        socket: null,
        connected: false,
        reconnectTimer: null,
        reconnectDelay: initialDelay,
        subscribers: new Set(),
      };
      wsRegistry.set(resolvedUrl, state);
    }

    const subscriber: WsSubscriber = {
      cbRef,
      setIsConnected,
      autoReconnect,
      initialDelay,
      maxDelay: maxReconnectDelay,
    };
    state.subscribers.add(subscriber);
    subscriberRef.current = subscriber;

    // Reflect current shared-connection state. Defer to a microtask so we
    // don't call setState synchronously in the effect body (lint rule).
    const connectedNow = state.connected;
    connectShared(resolvedUrl);
    if (connectedNow) {
      queueMicrotask(() => setIsConnected(true));
    }

    return () => {
      const s = wsRegistry.get(resolvedUrl);
      if (!s) return;
      s.subscribers.delete(subscriber);
      subscriberRef.current = null;
      if (s.subscribers.size === 0) {
        teardownShared(resolvedUrl, s);
      }
    };
    // connectShared/teardownShared are stable module-level functions.
  }, [enabled, resolvedUrl, autoReconnect, initialDelay, maxReconnectDelay]);

  // ---- Send ----
  const send = useCallback(
    (data: unknown) => {
      const state = wsRegistry.get(resolvedUrl);
      if (state?.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(JSON.stringify(data));
      }
    },
    [resolvedUrl],
  );

  // ---- Disconnect (this subscriber; if last, teardown socket) ----
  const disconnect = useCallback(() => {
    const state = wsRegistry.get(resolvedUrl);
    if (!state) return;
    if (subscriberRef.current) {
      state.subscribers.delete(subscriberRef.current);
      subscriberRef.current = null;
    }
    if (state.subscribers.size === 0) {
      teardownShared(resolvedUrl, state);
    }
    setIsConnected(false);
  }, [resolvedUrl]);

  // ---- Reconnect (manual) ----
  const reconnect = useCallback(() => {
    const existing = wsRegistry.get(resolvedUrl);
    if (existing) teardownShared(resolvedUrl, existing);
    connectShared(resolvedUrl);
  }, [resolvedUrl]);

  return { isConnected, lastMessage, send, disconnect, reconnect };
}

export default useWebSocket;

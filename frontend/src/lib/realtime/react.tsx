"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { type Connection, type RealtimeClient, type RealtimeEvent, createRealtimeClient } from "./client";

type Handler = (events: RealtimeEvent[], meta: { replay: boolean }) => void;
type Ctx = { connection: Connection; subscribe: (h: Handler) => () => void };

const Realtime = createContext<Ctx>({
  connection: { status: "connecting", transport: null },
  subscribe: () => () => {},
});

// The newest value for callbacks that outlive a render; set after commit so render stays pure.
function useLatest<T>(value: T) {
  const ref = useRef(value);
  useEffect(() => {
    ref.current = value;
  });
  return ref;
}

/** One feed per signed-in session. `make` lets tests supply a client wired to fakes. */
export function RealtimeProvider({
  children,
  make = createRealtimeClient,
  onReset,
}: {
  children: ReactNode;
  make?: typeof createRealtimeClient;
  onReset?: () => void;
}) {
  const [connection, setConnection] = useState<Connection>({ status: "connecting", transport: null });
  const handlers = useRef(new Set<Handler>());
  const reset = useLatest(onReset);

  useEffect(() => {
    const client: RealtimeClient = make({
      wsOrigin: process.env.NEXT_PUBLIC_WS_ORIGIN ?? null,
      onEvents: (events, meta) => handlers.current.forEach((h) => h(events, meta)),
      onState: setConnection,
      onReset: () => reset.current?.(),
    });
    client.start();
    return () => client.stop();
  }, [make, reset]);

  const subscribe = useCallback((h: Handler) => {
    handlers.current.add(h);
    return () => void handlers.current.delete(h);
  }, []);

  return <Realtime.Provider value={{ connection, subscribe }}>{children}</Realtime.Provider>;
}

export const useConnection = () => useContext(Realtime).connection;

/** Calls `handler` for each batch of live events (replayed history is skipped unless `replay` is asked for). */
export function useRealtimeEvents(handler: Handler, { replay = false } = {}) {
  const { subscribe } = useContext(Realtime);
  const latest = useLatest(handler);
  useEffect(
    () => subscribe((events, meta) => (replay || !meta.replay) && latest.current(events, meta)),
    [subscribe, replay, latest],
  );
}

/**
 * The open item changed elsewhere: flag it and re-read it. The re-read only refreshes server data; a draft lives in the
 * form's own state, so it is never overwritten. `dismiss` clears the notice once the reader has seen it.
 */
export function useUpdatedNotice(matches: (e: RealtimeEvent) => boolean, refetch: () => unknown) {
  const [updated, setUpdated] = useState(false);
  useRealtimeEvents((events) => {
    if (!events.some(matches)) return;
    setUpdated(true);
    void refetch();
  });
  return { updated, dismiss: () => setUpdated(false) };
}

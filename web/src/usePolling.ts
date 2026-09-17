import { useEffect, useRef } from "react";

/** Schedule after completion, so a slow request never creates a polling backlog. */
export function usePolling<T>(
  enabled: boolean,
  interval: number,
  request: () => Promise<T>,
  receive: (value: T) => void,
  fail: (error: unknown) => void,
) {
  const callbacks = useRef({ request, receive, fail });
  callbacks.current = { request, receive, fail };
  const pending = useRef(false);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (!active) return;
      if (!pending.current && document.visibilityState !== "hidden") {
        pending.current = true;
        try {
          const value = await callbacks.current.request();
          if (active) callbacks.current.receive(value);
        } catch (error) {
          if (active) callbacks.current.fail(error);
        } finally {
          pending.current = false;
        }
      }
      if (active) timer = setTimeout(poll, interval);
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [enabled, interval]);
}

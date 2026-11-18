import { options } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { markFeedsStale } from "../feed/useFeed";
import { getPreferences, PreferencesConflict, PreferencesOffline, putPreferences } from "./api";
import type { PreferencesResponse, PreferencesUpdate, PreferenceState } from "./types";

/** Debounce for saves, so rapid taps go out as one PUT. */
export const SAVE_DELAY_MS = 500;

export const OFFLINE_MESSAGE = "You're offline; changes need a connection";

/**
 * Runs `update` and renders its state changes before returning, so a tapped control shows its
 * new state at once rather than on the next frame (Preact's own `flushSync` is in compat).
 */
function renderNow(update: () => void): void {
  const prev = options.debounceRendering;
  let flush: (() => void) | undefined;
  options.debounceRendering = (cb) => {
    flush = cb;
  };
  try {
    update();
    flush?.();
  } finally {
    options.debounceRendering = prev;
  }
}

/** One state per row, keyed `s:<source id>` or `t:<topic id>`. */
type States = Record<string, PreferenceState>;

export const sourceKey = (id: string) => `s:${id}`;
export const topicKey = (id: string) => `t:${id}`;

function statesOf(r: PreferencesResponse): States {
  const out: States = {};
  for (const section of r.menu.sections) {
    for (const s of section.sources) out[sourceKey(s.id)] = s.state;
  }
  for (const t of r.menu.topics) out[topicKey(t.id)] = t.state;
  return out;
}

function bodyOf(states: States, version: number, setupDone: boolean): PreferencesUpdate {
  const body: PreferencesUpdate = { version, sources: {}, topics: {}, setup_done: setupDone };
  for (const [k, v] of Object.entries(states)) {
    if (v === "show") continue;
    if (k.startsWith("s:")) body.sources[k.slice(2)] = v;
    else body.topics[k.slice(2)] = v;
  }
  return body;
}

export interface UsePreferences {
  /** The menu's structure (sections, topics, labels), or null until loaded. */
  menu: PreferencesResponse["menu"] | null;
  /** A short message about a failed load or save, or "". */
  message: string;
  get: (key: string) => PreferenceState;
  set: (key: string, value: PreferenceState) => void;
  /** Saves now with `setup_done: true`; resolves whether or not the save worked. */
  finishSetup: () => Promise<void>;
}

/**
 * The owner's preferences, loaded once and saved as they change. Each change shows at once
 * and goes out debounced. A 409 means another device saved first: the owner's unsent and
 * in-flight changes are laid over the newer state and saved once more. Any other failure
 * puts the changed rows back. A PUT replaces the whole set, so after a 200 the server holds
 * exactly what was sent, and the rows stay as they are.
 */
export function usePreferences(): UsePreferences {
  const [menu, setMenu] = useState<PreferencesResponse["menu"] | null>(null);
  const [states, setStates] = useState<States>({});
  const [message, setMessage] = useState("");
  // Mutable save state, read by the timer and the in-flight request.
  const live = useRef<States>({});
  const saved = useRef<States>({}); // what the server holds, for putting rows back
  const version = useRef(0);
  const setupDone = useRef(false);
  const dirty = useRef(new Set<string>());
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const inFlight = useRef<Promise<void> | null>(null);

  const show = (next: States) => {
    live.current = next;
    setStates(next);
  };

  useEffect(() => {
    getPreferences()
      .then((r) => {
        version.current = r.version;
        setupDone.current = r.setup_done;
        saved.current = statesOf(r);
        show(saved.current);
        setMenu(r.menu);
      })
      .catch((err: unknown) => {
        // With no menu there are no controls, so nothing can be changed offline.
        if (err instanceof PreferencesOffline) setMessage(OFFLINE_MESSAGE);
        else setMessage("Could not load your preferences. Try again later.");
      });
    // Every screen is a full page load, so a change still in the debounce would be lost on
    // leaving: send it at once, with keepalive so the request outlives the page.
    const leave = () => {
      if (dirty.current.size > 0) void flush(true);
    };
    window.addEventListener("pagehide", leave);
    return () => {
      window.removeEventListener("pagehide", leave);
      leave();
    };
  }, []);

  async function send(keepalive: boolean): Promise<void> {
    const keys = new Set(dirty.current);
    dirty.current.clear();
    for (let attempt = 0; attempt < 2; attempt++) {
      const sent = live.current;
      try {
        const r = await putPreferences(bodyOf(sent, version.current, setupDone.current), { keepalive });
        version.current = r.version;
        setupDone.current = r.setup_done;
        saved.current = sent;
        setMessage("");
        markFeedsStale();
        return;
      } catch (err) {
        if (err instanceof PreferencesConflict && attempt === 0) {
          const next = statesOf(err.current);
          for (const k of [...keys, ...dirty.current]) {
            const v = live.current[k];
            if (v) next[k] = v;
          }
          version.current = err.current.version;
          setupDone.current = setupDone.current || err.current.setup_done;
          saved.current = statesOf(err.current);
          show(next);
          continue;
        }
        // Put back the rows this save carried, unless the owner has changed them again.
        const back = { ...live.current };
        for (const k of keys) if (!dirty.current.has(k)) back[k] = saved.current[k] ?? "show";
        show(back);
        setMessage(
          err instanceof PreferencesOffline ? OFFLINE_MESSAGE : "Could not save that change. Try again.",
        );
        console.error("preferences not saved", err);
        return;
      }
    }
  }

  /**
   * Runs one save after any in flight, so saves never overlap. With none in flight the request
   * starts before this returns, as a `pagehide` handler needs.
   */
  function flush(keepalive = false): Promise<void> {
    clearTimeout(timer.current);
    const prev = inFlight.current;
    const run: Promise<void> = (prev ? prev.then(() => send(keepalive)) : send(keepalive)).finally(
      () => {
        if (inFlight.current === run) inFlight.current = null;
      },
    );
    inFlight.current = run;
    return run;
  }

  function set(key: string, value: PreferenceState): void {
    renderNow(() => show({ ...live.current, [key]: value }));
    dirty.current.add(key);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), SAVE_DELAY_MS);
  }

  async function finishSetup(): Promise<void> {
    setupDone.current = true;
    await flush();
  }

  return {
    menu,
    message,
    get: (k) => states[k] ?? "show",
    set,
    finishSetup,
  };
}

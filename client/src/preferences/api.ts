/** The preferences API client (#338): `GET` and `PUT /api/preferences` (#337). */
import { apiFetch, OfflineError } from "../api/client";
import type { PreferencesResponse, PreferencesUpdate, PreferenceState } from "./types";

/** The save was based on an old version; `current` is the server's state now. */
export class PreferencesConflict extends Error {
  constructor(readonly current: PreferencesResponse) {
    super("Preferences changed elsewhere");
    this.name = "PreferencesConflict";
  }
}

/** The request did not reach the server (no signal). */
export class PreferencesOffline extends OfflineError {
  constructor() {
    super();
    this.name = "PreferencesOffline";
  }
}

/** Any other unexpected answer. */
export class PreferencesHttpError extends Error {
  constructor(readonly status: number) {
    super(`Preferences request failed with status ${status}`);
    this.name = "PreferencesHttpError";
  }
}

async function call(init: RequestInit): Promise<Response> {
  try {
    return await apiFetch("/api/preferences", init);
  } catch (err) {
    if (err instanceof OfflineError) throw new PreferencesOffline();
    throw err;
  }
}

/** Loads the owner's settings and the menu; throws `PreferencesOffline` with no signal. */
export async function getPreferences(): Promise<PreferencesResponse> {
  const response = await call({ method: "GET" });
  if (!response.ok) throw new PreferencesHttpError(response.status);
  return (await response.json()) as PreferencesResponse;
}

/**
 * Saves the whole set and returns the stored state. A 409 throws `PreferencesConflict` with the
 * newer state, so the caller can merge and retry. Every save sets `keepalive`, so one already in
 * flight when the page closes still finishes (#395); the body is far under the 64 KB limit.
 */
export async function putPreferences(
  body: PreferencesUpdate,
  // Unused on purpose: usePreferences.ts still passes it, and changing that is out of scope for #395.
  // The flag is now always on, so the disable below is justified.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _opts: { keepalive?: boolean } = {},
): Promise<PreferencesResponse> {
  const response = await call({
    method: "PUT",
    keepalive: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.status === 409) {
    throw new PreferencesConflict((await response.json()) as PreferencesResponse);
  }
  if (!response.ok) throw new PreferencesHttpError(response.status);
  return (await response.json()) as PreferencesResponse;
}

/** The body that saves `current` with `changes` applied; only non-Show states are sent. */
function withChanges(
  current: PreferencesResponse,
  changes: Record<string, "hide" | "show">,
): PreferencesUpdate {
  const sources: Record<string, PreferenceState> = {};
  const topics: Record<string, PreferenceState> = {};
  for (const section of current.menu.sections) {
    for (const s of section.sources) if (s.state !== "show") sources[s.id] = s.state;
  }
  for (const t of current.menu.topics) if (t.state !== "show") topics[t.id] = t.state;
  for (const [id, state] of Object.entries(changes)) {
    if (state === "show") delete sources[id];
    else sources[id] = state;
  }
  return { version: current.version, sources, topics, setup_done: current.setup_done };
}

/**
 * Sets several sources at once (#339): reads the current preferences, PUTs them with the changes,
 * and on a 409 retries once on the newer state. Throws `PreferencesOffline` with no signal.
 */
export async function setSourceStates(changes: Record<string, "hide" | "show">): Promise<void> {
  const current = await getPreferences();
  try {
    await putPreferences(withChanges(current, changes));
  } catch (err) {
    if (!(err instanceof PreferencesConflict)) throw err;
    await putPreferences(withChanges(err.current, changes));
  }
}

/** Sets one source to Hide or Show, leaving every other setting as the server has it (#339). */
export function setSourceState(sourceId: string, state: "hide" | "show"): Promise<void> {
  return setSourceStates({ [sourceId]: state });
}

/** The preferences API client (#338): `GET` and `PUT /api/preferences` (#337). */
import { apiFetch, OfflineError } from "../api/client";
import type { PreferencesResponse, PreferencesUpdate } from "./types";

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

export async function getPreferences(): Promise<PreferencesResponse> {
  const response = await call({ method: "GET" });
  if (!response.ok) throw new PreferencesHttpError(response.status);
  return (await response.json()) as PreferencesResponse;
}

/** Saves the whole set; a 409 carries the newer state so the caller can merge and retry. */
export async function putPreferences(body: PreferencesUpdate): Promise<PreferencesResponse> {
  const response = await call({
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.status === 409) {
    throw new PreferencesConflict((await response.json()) as PreferencesResponse);
  }
  if (!response.ok) throw new PreferencesHttpError(response.status);
  return (await response.json()) as PreferencesResponse;
}

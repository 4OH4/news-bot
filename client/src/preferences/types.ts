/** The `/api/preferences` contract (#337), as the client reads and writes it. */
import type { PreferenceState } from "./PreferenceControl";

export type { PreferenceState };

export interface MenuSource {
  id: string;
  label: string;
  state: PreferenceState;
  starter: boolean;
}

export interface MenuSection {
  id: string;
  label: string;
  sources: MenuSource[];
}

export interface MenuTopic {
  id: string;
  label: string;
  state: PreferenceState;
  starter: boolean;
}

export interface PreferencesResponse {
  version: number;
  setup_done: boolean;
  menu: {
    sections: MenuSection[];
    topics: MenuTopic[];
    multipliers: Record<PreferenceState, number>;
  };
}

/** A full replacement: every source and topic whose state isn't Show. */
export interface PreferencesUpdate {
  version: number;
  sources: Record<string, PreferenceState>;
  topics: Record<string, PreferenceState>;
  setup_done: boolean;
}

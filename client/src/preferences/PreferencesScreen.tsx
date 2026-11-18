import { useState } from "preact/hooks";
import { PreferenceControl } from "./PreferenceControl";
import "./preferences.css";
import type { MenuSource, MenuTopic } from "./types";
import { sourceKey, topicKey, usePreferences, type UsePreferences } from "./usePreferences";

const NOTE =
  "Hide works on every tab. Fewer and More change the order in Feed and Longer; News stays newest first.";

type Entry = (MenuSource | MenuTopic) & { key: string };

/** Starter rows, then a "Show n more" button that opens the rest; no button if none are left. */
function Group({ prefs, heading, entries }: { prefs: UsePreferences; heading: string; entries: Entry[] }) {
  const [open, setOpen] = useState(false);
  const rest = entries.filter((e) => !e.starter).length;
  const shown = open ? entries : entries.filter((e) => e.starter);
  return (
    <section>
      <h2>{heading}</h2>
      {shown.length > 0 ? (
        <ul class="prefs-list">
          {shown.map((e) => (
            <li key={e.key} class="prefs-row">
              <span class="prefs-name">{e.label}</span>
              <PreferenceControl label={e.label} value={prefs.get(e.key)} onChange={(v) => prefs.set(e.key, v)} />
            </li>
          ))}
        </ul>
      ) : null}
      {rest > 0 ? (
        <button type="button" class="prefs-more" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Show fewer" : `Show ${rest} more`}
        </button>
      ) : null}
    </section>
  );
}

/**
 * The preferences screen at /settings/preferences (#338): the owner's pick of the prototype's
 * layout a (#333). One section per source section the API returns, in its order, then Topics.
 * With `setup`, it is the first-run screen, and "Start reading" finishes setup.
 */
export function PreferencesScreen({ setup }: { setup: boolean }) {
  const prefs = usePreferences();
  const [starting, setStarting] = useState(false);
  const { menu } = prefs;

  const start = async () => {
    setStarting(true);
    // The feed opens either way; if the save failed, setup is offered again next start.
    await prefs.finishSetup();
    location.replace("/");
  };

  return (
    <main class="prefs">
      {setup ? (
        <header>
          <h1>Welcome</h1>
          <p class="prefs-intro">Choose what you see. You can change this any time in Settings.</p>
        </header>
      ) : (
        <h1>Sources and topics</h1>
      )}
      <p class="prefs-note">{NOTE}</p>
      {prefs.message ? <p role="status" class="prefs-message">{prefs.message}</p> : null}
      {menu ? (
        <>
          {menu.sections.map((section) => (
            <Group
              key={section.id}
              prefs={prefs}
              heading={section.label}
              entries={section.sources.map((s) => ({ ...s, key: sourceKey(s.id) }))}
            />
          ))}
          <Group
            prefs={prefs}
            heading="Topics"
            entries={menu.topics.map((t) => ({ ...t, key: topicKey(t.id) }))}
          />
        </>
      ) : null}
      {setup ? (
        <button type="button" class="prefs-start" disabled={starting} onClick={() => void start()}>
          Start reading
        </button>
      ) : null}
    </main>
  );
}

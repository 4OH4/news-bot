import { useEffect, useState } from "preact/hooks";
import { signOut } from "../auth/signout";
import type { FeedItem } from "../feed/types";
import { setThemeChoice, themeChoice, type ThemeChoice } from "../reader/theme";
import { fontSize, setFontSize, type FontSize } from "./fontSize";
import { listMuted, subscribeHidden, unmuteSource } from "../feedback/hidden";

const THEME_OPTIONS: [ThemeChoice, string][] = [
  ["system", "Follow system"],
  ["light", "Light"],
  ["dark", "Dark"],
];

const FONT_SIZE_OPTIONS: [FontSize, string][] = [
  ["small", "Small"],
  ["regular", "Regular"],
  ["large", "Large"],
  ["xl", "XL"],
];

/**
 * Settings at /settings (#217): muted sources with Unmute, Sign out (through #184's guard, so
 * unsent feedback is asked about first) and the app version.
 */
export function SettingsScreen() {
  const [muted, setMuted] = useState<FeedItem[] | null>(null);
  const [theme, setTheme] = useState<ThemeChoice>(themeChoice);
  const [size, setSize] = useState<FontSize>(fontSize);

  useEffect(() => {
    const load = () =>
      listMuted()
        .then(setMuted)
        .catch((err: unknown) => {
          console.error("could not read muted sources", err);
          setMuted([]);
        });
    void load();
    return subscribeHidden(() => void load());
  }, []);

  const unmute = (item: FeedItem) => {
    unmuteSource(item).catch((err: unknown) =>
      console.error("could not unmute", { source_id: item.source_id, err }),
    );
  };

  return (
    <main>
      <h1>Settings</h1>
      <h2>Muted sources</h2>
      {muted === null ? null : muted.length === 0 ? (
        <p>No sources muted. Mute one from a story's menu on the feed.</p>
      ) : (
        <ul class="muted-list">
          {muted.map((item) => (
            <li key={item.source_id}>
              <span>{item.domain}</span>{" "}
              <button
                type="button"
                aria-label={`Unmute ${item.domain}`}
                onClick={() => unmute(item)}
              >
                Unmute
              </button>
            </li>
          ))}
        </ul>
      )}
      <h2>Theme</h2>
      <div role="radiogroup" aria-label="Theme">
        {THEME_OPTIONS.map(([value, label]) => (
          <label key={value}>
            <input
              type="radio"
              name="theme"
              value={value}
              checked={theme === value}
              onChange={() => {
                setThemeChoice(value);
                setTheme(value);
              }}
            />{" "}
            {label}
          </label>
        ))}
      </div>
      <h2>Text size</h2>
      <div role="radiogroup" aria-label="Text size">
        {FONT_SIZE_OPTIONS.map(([value, label]) => (
          <label key={value}>
            <input
              type="radio"
              name="font-size"
              value={value}
              checked={size === value}
              onChange={() => {
                setFontSize(value);
                setSize(value);
              }}
            />{" "}
            {label}
          </label>
        ))}
      </div>
      <h2>Account</h2>
      <button type="button" onClick={() => void signOut()}>
        Sign out
      </button>
      <p>Version {__APP_VERSION__}</p>
    </main>
  );
}

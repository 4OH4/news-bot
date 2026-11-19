import { useState } from "preact/hooks";
import { signOut } from "../auth/signout";
import { setThemeChoice, themeChoice, type ThemeChoice } from "../reader/theme";
import { fontSize, setFontSize, type FontSize } from "./fontSize";

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
 * Settings at /settings (#217): links to liked articles and to "Sources and topics" (where a
 * hidden source is shown and undone, #338), theme, text size, ranking comparison, Sign out
 * (through #184's guard, so unsent feedback is asked about first) and the app version.
 */
export function SettingsScreen() {
  const [theme, setTheme] = useState<ThemeChoice>(themeChoice);
  const [size, setSize] = useState<FontSize>(fontSize);

  return (
    <main>
      <h1>Settings</h1>
      <p>
        <a href="/liked">Liked articles</a>
      </p>
      <p>
        <a href="/settings/preferences">Sources and topics</a>
      </p>
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
      <h2>Compare ranking</h2>
      <p>
        <a href="/?ranking=rules">Feed, rules only</a>
      </p>
      <p>
        <a href="/?ranking=personal">Feed, personalised only</a>
      </p>
      <h2>Account</h2>
      <button type="button" onClick={() => void signOut()}>
        Sign out
      </button>
      <p>Version {__APP_VERSION__}</p>
    </main>
  );
}

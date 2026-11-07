import { useEffect, useState } from "preact/hooks";
import { signOut } from "../auth/signout";
import type { FeedItem } from "../feed/types";
import { listMuted, subscribeHidden, unmuteSource } from "../feedback/hidden";

/**
 * Settings at /settings (#217): muted sources with Unmute, Sign out (through #184's guard, so
 * unsent feedback is asked about first) and the app version.
 */
export function SettingsScreen() {
  const [muted, setMuted] = useState<FeedItem[] | null>(null);

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

  const unmute = (sourceId: string) => {
    unmuteSource(sourceId).catch((err: unknown) =>
      console.error("could not unmute", { source_id: sourceId, err }),
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
                onClick={() => unmute(item.source_id)}
              >
                Unmute
              </button>
            </li>
          ))}
        </ul>
      )}
      <h2>Account</h2>
      <button type="button" onClick={() => void signOut()}>
        Sign out
      </button>
      <p>Version {__APP_VERSION__}</p>
    </main>
  );
}

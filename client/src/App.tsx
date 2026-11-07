import { useEffect } from "preact/hooks";
import { apiFetch } from "./api/client";
import { NotAllowed } from "./auth/NotAllowed";
import { ReconnectBanner } from "./auth/ReconnectBanner";
import { useSessionStatus } from "./auth/useSessionStatus";

/** Placeholder screen: real screens arrive with #181 to #184. */
export function App() {
  const status = useSessionStatus();
  useEffect(() => {
    // Startup call: a lapsed or refused session shows up here. Errors are already
    // reported to the status store, so there is nothing more to do with them.
    apiFetch("/api/feed").catch(() => {});
  }, []);
  return (
    <>
      <ReconnectBanner />
      {status === "not-allowed" ? (
        <NotAllowed />
      ) : (
        <main>
          <h1>Newsfeed</h1>
          <p id="status">The app is running.</p>
          <p id="version" data-version={__APP_VERSION__}>
            Build {__APP_VERSION__}
          </p>
        </main>
      )}
    </>
  );
}

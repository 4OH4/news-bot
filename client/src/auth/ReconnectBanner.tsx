import { reconnect } from "./reconnect";
import { useSessionStatus } from "./useSessionStatus";

/** Shown over whatever is on screen while the session has lapsed and we are online. */
export function ReconnectBanner() {
  if (useSessionStatus() !== "reconnect") return null;
  return (
    <div role="alert" class="banner">
      <span>Your session has expired.</span>
      <button type="button" onClick={reconnect}>
        Reconnect
      </button>
    </div>
  );
}

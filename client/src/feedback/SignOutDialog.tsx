/**
 * Asked before a sign-out while feedback is unsent or rejected (#184). It resolves `true` to let
 * the sign-out go ahead (its hooks then clear the feedback stores) or `false` to stay signed in.
 */
import { useState } from "preact/hooks";
import { useSessionStatus } from "../auth/useSessionStatus";
import "./dialog.css";

export interface Counts {
  queued: number;
  rejected: number;
}

export interface SignOutDialogProps {
  initial: Counts;
  /** Sends the queue and returns the counts afterwards. */
  send: () => Promise<Counts>;
  /** Called once with the answer; the dialog then shows nothing more unless it says why. */
  done: (proceed: boolean) => void;
  /** Removes the dialog. */
  close: () => void;
}

function events(n: number): string {
  return n === 1 ? "1 feedback event" : `${n} feedback events`;
}

export function SignOutDialog({ initial, send, done, close }: SignOutDialogProps) {
  const [counts, setCounts] = useState(initial);
  const [state, setState] = useState<"ask" | "sending" | "failed">("ask");
  // A 403 also blocks the send, so discarding then would delete feedback the server never had.
  const canDiscard = useSessionStatus() !== "not-allowed";

  const finish = (proceed: boolean) => {
    done(proceed);
    close();
  };

  const onSend = async () => {
    setState("sending");
    let after: Counts;
    try {
      after = await send();
    } catch {
      after = counts;
    }
    setCounts(after);
    if (after.queued === 0 && after.rejected === 0) return finish(true);
    if (after.queued === 0) return setState("ask");
    // Stay signed in; the dialog now only says why.
    done(false);
    setState("failed");
  };

  if (state === "failed") {
    return (
      <div class="signout-dialog" role="alertdialog" aria-labelledby="signout-dialog-title">
        <h2 id="signout-dialog-title">Still signed in</h2>
        <p>
          Your feedback hasn't been sent, so you are still signed in.{" "}
          {canDiscard
            ? "It will be sent when the connection works."
            : "This account can't send it now; it is kept on this phone until it can."}
        </p>
        <div class="signout-dialog-actions">
          <button type="button" onClick={close}>
            OK
          </button>
        </div>
      </div>
    );
  }

  const busy = state === "sending";
  return (
    <div class="signout-dialog" role="alertdialog" aria-labelledby="signout-dialog-title">
      <h2 id="signout-dialog-title">Sign out?</h2>
      {counts.queued > 0 && <p>{events(counts.queued)} waiting to go to the server.</p>}
      {counts.rejected > 0 && (
        <p>{events(counts.rejected)} the server refused. They won't be tried again.</p>
      )}
      <p>
        {canDiscard
          ? "Signing out deletes them from this phone."
          : "This account can't send them now, so they are kept until it can."}
      </p>
      <div class="signout-dialog-actions">
        {counts.queued > 0 && (
          <button type="button" disabled={busy} onClick={() => void onSend()}>
            {busy ? "Sending…" : "Send now"}
          </button>
        )}
        {canDiscard && (
          <button type="button" disabled={busy} onClick={() => finish(true)}>
            Discard and sign out
          </button>
        )}
        <button type="button" disabled={busy} onClick={() => finish(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

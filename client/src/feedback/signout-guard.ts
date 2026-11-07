/**
 * The sign-out guard (#184). `signOut` (#180) awaits it on every entry point, so no sign-out
 * clears unsent or rejected feedback unless the owner chose to discard it.
 *
 * The guard records the ids of the events it counted (or last showed in the dialog), and the
 * sign-out hook deletes only those. Enqueues are never held back, so an event created while the
 * dialog is open, or after the count, is written at once and survives the sign-out.
 */
import { h, render } from "preact";
import { setSignOutGuard } from "../auth/signout";
import { clearCountedFeedback, type CountedIds } from "./db";
import { SignOutDialog, type Counts } from "./SignOutDialog";
import { flushQueue, unsentIds } from "./queue";

const NONE: CountedIds = { queued: [], rejected: [] };

/** What the guard last counted or showed; `null` until a sign-out has gone ahead. */
let counted: CountedIds | null = null;

function ask(initial: CountedIds): Promise<CountedIds | null> {
  let shown = initial;
  const countsOf = (ids: CountedIds): Counts => ({
    queued: ids.queued.length,
    rejected: ids.rejected.length,
  });
  return new Promise<CountedIds | null>((resolve) => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const close = () => {
      render(null, host);
      host.remove();
    };
    const send = async () => {
      await flushQueue();
      shown = await unsentIds();
      return countsOf(shown);
    };
    // Discard (or a send that emptied both stores) deletes what the dialog last showed.
    const done = (proceed: boolean) => resolve(proceed ? shown : null);
    render(h(SignOutDialog, { initial: countsOf(initial), send, done, close }), host);
  });
}

/** Resolves the ids to delete if the sign-out goes ahead, or `null` to stay signed in. */
async function decide(): Promise<CountedIds | null> {
  const now = await unsentIds();
  if (now.queued.length === 0 && now.rejected.length === 0) return NONE;
  return ask(now);
}

let pending: Promise<boolean> | null = null;

/** Resolves `true` at once only when both the queue and the rejected events are empty. */
function guard(): Promise<boolean> {
  // A second tap while the dialog is open gets the same answer, not a second dialog.
  if (!pending) {
    pending = decide()
      .then((ids) => {
        counted = ids;
        return ids !== null;
      })
      .finally(() => {
        pending = null;
      });
  }
  return pending;
}

export function installSignOutGuard(): void {
  setSignOutGuard(guard);
}

/**
 * The sign-out hook (`start.ts`): deletes the events the guard counted and the per-story state.
 * Without a guard decision it deletes no events, only the per-story state.
 */
export async function clearOnSignOut(): Promise<void> {
  const ids = counted ?? NONE;
  counted = null;
  await clearCountedFeedback(ids);
}

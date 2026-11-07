/**
 * The sign-out guard (#184). `signOut` (#180) awaits it on every entry point, so no sign-out
 * clears unsent or rejected feedback unless the owner chose to discard it.
 */
import { h, render } from "preact";
import { setSignOutGuard } from "../auth/signout";
import { SignOutDialog, type Counts } from "./SignOutDialog";
import { flushQueue, queuedCount, rejectedCount } from "./queue";

async function counts(): Promise<Counts> {
  return { queued: await queuedCount(), rejected: await rejectedCount() };
}

let pending: Promise<boolean> | null = null;

function ask(initial: Counts): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const close = () => {
      render(null, host);
      host.remove();
    };
    const send = async () => {
      await flushQueue();
      return counts();
    };
    render(h(SignOutDialog, { initial, send, done: resolve, close }), host);
  });
}

/** Resolves `true` at once only when both the queue and the rejected events are empty. */
async function guard(): Promise<boolean> {
  // A second tap while the dialog is open gets the same answer, not a second dialog.
  if (pending) return pending;
  const now = await counts();
  if (now.queued === 0 && now.rejected === 0) return true;
  if (pending) return pending;
  pending = ask(now).finally(() => {
    pending = null;
  });
  return pending;
}

export function installSignOutGuard(): void {
  setSignOutGuard(guard);
}

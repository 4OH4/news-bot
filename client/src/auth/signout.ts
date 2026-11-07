/** The only module that names the IAP clear-cookie URL. */
const CLEAR_COOKIE_URL = "/?gcp-iap-mode=CLEAR_LOGIN_COOKIE";

type Hook = () => Promise<void>;
const hooks: Hook[] = [];
let guard: (() => Promise<boolean>) | null = null;

/** Registers local-data clearing to run on a sign-out the user chose. */
export function onSignOut(fn: Hook): void {
  hooks.push(fn);
}

/** The guard may veto a sign-out (for example, unsent feedback); `false` cancels it. */
export function setSignOutGuard(fn: () => Promise<boolean>): void {
  guard = fn;
}

/**
 * A sign-out the user chose; every entry point calls this. The clear-cookie URL is fetched,
 * never navigated to: reloading it straight after clearing produced IAP error 11 in #196.
 */
export async function signOut(): Promise<void> {
  if (guard && !(await guard())) return;
  for (const hook of hooks) await hook();
  try {
    await fetch(CLEAR_COOKIE_URL, {
      credentials: "same-origin",
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    // The answer is ignored; offline we still leave the app.
  }
  location.replace("/");
}

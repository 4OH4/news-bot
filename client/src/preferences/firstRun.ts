import { getPreferences } from "./api";

export const SETUP_PATH = "/settings/preferences?setup=1";

// Once per app session: if the setup save fails, the feed still opens, and the next start asks again.
const ASKED_KEY = "newsfeed-setup-asked";

function askedThisSession(): boolean {
  try {
    return sessionStorage.getItem(ASKED_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * First run (#338): when the server says setup isn't done, opens the preferences screen as the
 * setup screen, once. Any failure (offline, an error) leaves the feed where it is.
 */
export async function openSetupIfNeeded(): Promise<void> {
  if (askedThisSession()) return;
  let setupDone: boolean;
  try {
    setupDone = (await getPreferences()).setup_done;
  } catch {
    return;
  }
  if (setupDone) return;
  try {
    sessionStorage.setItem(ASKED_KEY, "1");
  } catch {
    // Without storage the screen can show again on the next load; Start reading still ends it.
  }
  location.replace(SETUP_PATH);
}

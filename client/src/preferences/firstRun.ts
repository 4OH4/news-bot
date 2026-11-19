import { sessionStatus } from "../auth/status";
import { firstFeedLoad } from "../feed/useFeed";
import { getPreferences } from "./api";

export const SETUP_PATH = "/settings/preferences?setup=1";

// Once per app session, after the server has answered: a failed request asks again on the next
// load, and if the setup save fails, the feed still opens and the next start asks again.
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
 * setup screen, once. The server is asked at most once per session after it has answered, so a
 * feed tab load once setup is done sends no request. It waits for the first feed load, and skips
 * while the session needs attention, so its answer can't race the feed's 401. Any failure
 * leaves the feed as it is.
 */
export async function openSetupIfNeeded(): Promise<void> {
  if (askedThisSession()) return;
  await firstFeedLoad;
  if (sessionStatus !== "ok") return;
  let setupDone: boolean;
  try {
    setupDone = (await getPreferences()).setup_done;
  } catch {
    return;
  }
  try {
    sessionStorage.setItem(ASKED_KEY, "1");
  } catch {
    // Without storage the screen can show again on the next load; Start reading still ends it.
  }
  if (setupDone) return;
  location.replace(SETUP_PATH);
}

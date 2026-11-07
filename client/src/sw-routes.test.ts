import { describe, expect, it } from "vitest";
import { isNetworkOnly } from "./sw-routes";

describe("isNetworkOnly", () => {
  it.each([
    "/api/feed",
    "/reconnect",
    "/reconnect?x=1",
    "/_gcp_iap/clear_login_cookie",
    "/?gcp-iap-mode=CLEAR_LOGIN_COOKIE",
    "/anything?a=1&gcp-iap-mode=DO_SESSION_REFRESH",
  ])("sends %s to the network", (path) => {
    expect(isNetworkOnly(path)).toBe(true);
  });

  it.each(["/", "/index.html", "/article/42", "/reconnected-story", "/apix"])(
    "lets %s fall back to the shell",
    (path) => {
      expect(isNetworkOnly(path)).toBe(false);
    },
  );
});

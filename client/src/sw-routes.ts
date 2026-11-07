/**
 * Navigations the service worker must never answer from the precached shell.
 *
 * Workbox tests each pattern against the path plus query string. Keep `/reconnect` here
 * and in `auth/reconnect.ts` only (docs/phases/phase-3.md, "Reconnect"), so a different
 * Reconnect method changes one list.
 */
export const NETWORK_ONLY_NAVIGATIONS: readonly RegExp[] = [
  /^\/api\//,
  /^\/reconnect(?:[/?#]|$)/,
  /^\/_gcp_iap\//,
  /[?&]gcp-iap-mode=/,
];

/** True when a navigation to `pathAndSearch` must go to the network. */
export function isNetworkOnly(pathAndSearch: string): boolean {
  return NETWORK_ONLY_NAVIGATIONS.some((re) => re.test(pathAndSearch));
}

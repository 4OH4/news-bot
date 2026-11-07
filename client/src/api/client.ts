import { reportAuthRequired, reportNotAllowed, reportOk } from "../auth/status";

/** IAP refused the session (401), or answered with a redirect despite the header. */
export class AuthRequiredError extends Error {
  constructor() {
    super("Sign-in required");
    this.name = "AuthRequiredError";
  }
}

/** The account is signed in but not allowed to use the app (403). */
export class NotAllowedError extends Error {
  constructor() {
    super("Account not allowed");
    this.name = "NotAllowedError";
  }
}

/** The request did not complete (network down). */
export class OfflineError extends Error {
  constructor() {
    super("Offline");
    this.name = "OfflineError";
  }
}

/**
 * Calls the API. `X-Requested-With` makes IAP answer a lapsed session with 401 instead of a
 * redirect to Google's sign-in page. Nothing here logs a header or cookie value.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-Requested-With", "XMLHttpRequest");
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
      redirect: "manual",
    });
  } catch (err) {
    if (err instanceof TypeError) throw new OfflineError();
    throw err;
  }
  if (response.type === "opaqueredirect" || response.status === 401) {
    reportAuthRequired();
    throw new AuthRequiredError();
  }
  if (response.status === 403) {
    reportNotAllowed();
    throw new NotAllowedError();
  }
  if ((response.status >= 200 && response.status < 300) || response.status === 304) reportOk();
  return response;
}

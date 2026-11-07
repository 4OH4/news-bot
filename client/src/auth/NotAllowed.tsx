import { signOut } from "./signout";

/** Shown instead of the app while the signed-in account is refused (403). */
export function NotAllowed() {
  return (
    <main class="not-allowed">
      <h1>Not allowed</h1>
      <p>This account can't use this app.</p>
      <button type="button" onClick={() => void signOut()}>
        Sign out
      </button>
    </main>
  );
}

import { NotAllowed } from "./auth/NotAllowed";
import { ReconnectBanner } from "./auth/ReconnectBanner";
import { useSessionStatus } from "./auth/useSessionStatus";
import { FeedScreen } from "./feed/FeedScreen";

/** Placeholder until the reader arrives with #182. */
function ArticlePlaceholder() {
  return (
    <main>
      <p>
        <a href="/">Back to the feed</a>
      </p>
      <p>The reader is not built yet.</p>
    </main>
  );
}

export function App() {
  const status = useSessionStatus();
  const isArticle = location.pathname.startsWith("/article/");
  return (
    <>
      <ReconnectBanner />
      {status === "not-allowed" ? (
        <NotAllowed />
      ) : (
        <>
          {isArticle ? <ArticlePlaceholder /> : <FeedScreen />}
          <footer class="footer">
            <p id="status">The app is running.</p>
            <p id="version" data-version={__APP_VERSION__}>
              Build {__APP_VERSION__}
            </p>
          </footer>
        </>
      )}
    </>
  );
}

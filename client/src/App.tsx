import { useEffect } from "preact/hooks";
import { NotAllowed } from "./auth/NotAllowed";
import { ReconnectBanner } from "./auth/ReconnectBanner";
import { useSessionStatus } from "./auth/useSessionStatus";
import { FeedScreen } from "./feed/FeedScreen";
import { movePendingReads } from "./feedback/reading";
import { LikedScreen } from "./liked/LikedScreen";
import "./liked/liked.css";
import { ReaderScreen } from "./reader/ReaderScreen";
import { restoreListScroll, saveListScroll } from "./reader/scroll";
import { applyStoredTheme } from "./reader/theme";
import { SettingsScreen } from "./settings/SettingsScreen";

applyStoredTheme();

function Screen({ path }: { path: string }) {
  if (path.startsWith("/article/")) return <ReaderScreen />;
  if (path === "/liked" || path === "/liked/") return <LikedScreen />;
  if (path === "/settings" || path === "/settings/") return <SettingsScreen />;
  return <FeedScreen />;
}

export function App() {
  const status = useSessionStatus();
  const isArticle = location.pathname.startsWith("/article/");
  // A reading the last page finished as it unloaded is queued here (see feedback/reading.ts).
  useEffect(() => {
    void movePendingReads();
  }, []);
  useEffect(() => {
    if (isArticle) return;
    restoreListScroll();
    // Save on the click that leaves for an article; by pagehide the scroll position is gone.
    const onClick = (e: MouseEvent) => {
      if (e.target instanceof Element && e.target.closest('a[href^="/article/"]')) saveListScroll();
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [isArticle]);
  return (
    <>
      <ReconnectBanner />
      {status === "not-allowed" ? (
        <NotAllowed />
      ) : (
        <>
          {isArticle ? null : (
            <header class="app-header">
              <nav aria-label="Sections">
                <a href="/">Feed</a>
                <a href="/liked">Liked</a>
                <a href="/settings">Settings</a>
              </nav>
            </header>
          )}
          <Screen path={location.pathname} />
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

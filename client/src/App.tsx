import { useEffect } from "preact/hooks";
import { NotAllowed } from "./auth/NotAllowed";
import { ReconnectBanner } from "./auth/ReconnectBanner";
import { useSessionStatus } from "./auth/useSessionStatus";
import { FeedScreen } from "./feed/FeedScreen";
import { LikedScreen } from "./liked/LikedScreen";
import "./liked/liked.css";
import { ReaderScreen } from "./reader/ReaderScreen";
import { restoreListScroll, saveListScroll } from "./reader/scroll";
import { applyStoredTheme } from "./reader/theme";
import { applyStoredFontSize } from "./settings/fontSize";
import { SettingsScreen } from "./settings/SettingsScreen";

applyStoredTheme();
applyStoredFontSize();

function Screen({ path }: { path: string }) {
  if (path.startsWith("/article/")) return <ReaderScreen />;
  if (path === "/liked" || path === "/liked/") return <LikedScreen />;
  if (path === "/settings" || path === "/settings/") return <SettingsScreen />;
  return <FeedScreen />;
}

const TABS = [
  { href: "/", label: "Feed" },
  { href: "/liked", label: "Liked" },
  { href: "/settings", label: "Settings" },
];

/** The current section's href; trailing-slash variants count as the same screen, as in `Screen`. */
function currentTab(path: string): string {
  if (path === "/liked" || path === "/liked/") return "/liked";
  if (path === "/settings" || path === "/settings/") return "/settings";
  return "/";
}

function TabBar({ path }: { path: string }) {
  const now = currentTab(path);
  return (
    <nav class="tab-bar" aria-label="Sections">
      <div class="tab-bar-inner">
        {TABS.map((t) => (
          <a key={t.href} href={t.href} aria-current={t.href === now ? "page" : undefined}>
            {t.label}
          </a>
        ))}
      </div>
    </nav>
  );
}

export function App() {
  const status = useSessionStatus();
  const isArticle = location.pathname.startsWith("/article/");
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
          <Screen path={location.pathname} />
          {isArticle ? null : (
            <>
              <p id="version" data-version={__APP_VERSION__} hidden />
              <TabBar path={location.pathname} />
            </>
          )}
        </>
      )}
    </>
  );
}

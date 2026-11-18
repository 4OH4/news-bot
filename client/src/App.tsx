import { useEffect } from "preact/hooks";
import { NotAllowed } from "./auth/NotAllowed";
import { ReconnectBanner } from "./auth/ReconnectBanner";
import { useSessionStatus } from "./auth/useSessionStatus";
import { FeedScreen } from "./feed/FeedScreen";
import { PreferencesLab } from "./lab/PreferencesLab";
import { LikedScreen } from "./liked/LikedScreen";
import { openSetupIfNeeded } from "./preferences/firstRun";
import { PreferencesScreen } from "./preferences/PreferencesScreen";
import "./liked/liked.css";
import { ReaderScreen } from "./reader/ReaderScreen";
import { restoreListScroll, saveListScroll } from "./reader/scroll";
import { applyStoredTheme } from "./reader/theme";
import { applyStoredFontSize } from "./settings/fontSize";
import { SettingsScreen } from "./settings/SettingsScreen";

applyStoredTheme();
applyStoredFontSize();

/** The path without trailing slashes, so `/news/` and `/news` are the same screen. */
function clean(path: string): string {
  return path.replace(/\/+$/, "") || "/";
}

function Screen({ path }: { path: string }) {
  if (path.startsWith("/article/")) return <ReaderScreen />;
  const p = clean(path);
  if (p === "/lab/preferences") return <PreferencesLab />;
  if (p === "/liked") return <LikedScreen />;
  if (p === "/settings") return <SettingsScreen />;
  if (p === "/settings/preferences") {
    return <PreferencesScreen setup={new URLSearchParams(location.search).get("setup") === "1"} />;
  }
  if (p === "/news") return <FeedScreen view="news" />;
  if (p === "/longer") return <FeedScreen view="longer" />;
  return <FeedScreen view="feed" />;
}

const TABS = [
  { href: "/news", label: "News" },
  { href: "/", label: "Feed" },
  { href: "/longer", label: "Longer" },
];

/** The current tab's href, or null on Liked and Settings, which are not tabs (#289). */
function currentTab(path: string): string | null {
  const p = clean(path);
  if (p === "/liked" || p === "/settings" || p.startsWith("/settings/")) return null;
  return p === "/news" || p === "/longer" ? p : "/";
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
  const isLab = clean(location.pathname).startsWith("/lab/");
  const isFeed = ["/", "/news", "/longer"].includes(clean(location.pathname));
  useEffect(() => {
    // The first feed screen of a fresh install opens setup first (#338).
    if (isFeed) void openSetupIfNeeded();
  }, [isFeed]);
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
          {isArticle || isLab ? null : (
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

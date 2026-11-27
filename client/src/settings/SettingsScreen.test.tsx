// @vitest-environment happy-dom
//
// The Settings screen at /settings: the Liked and Sources-and-topics links, no "Muted sources"
// list (#407), Sign out through #184's guard, and the app version.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buttonsNamed,
  installRouter,
  loadStores,
  locationAt,
  makeItem,
  queuedEvents,
  settle,
} from "../../test/test-env";

const KEPT = makeItem("b1c2d3e4f5a6b7c8", {
  title: "Invented story about ferry timetables",
  source_id: "invented-ferries",
  domain: "ferries.example.test",
});

let root: HTMLElement;
let here: ReturnType<typeof locationAt>;

beforeEach(() => {
  here = locationAt("/settings");
  vi.stubGlobal("location", here);
  vi.stubGlobal("__APP_VERSION__", "9.8.7-invented");
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function showSettings(): Promise<void> {
  const { SettingsScreen } = await import("./SettingsScreen");
  render(h(SettingsScreen, null), root);
}

describe("SettingsScreen", () => {
  // #307: the heart left the feed header, so Settings is the way to the Liked screen.
  it("has a plain Liked articles link to /liked", async () => {
    installRouter({ online: false });
    await loadStores();
    await showSettings();
    const link = [...root.querySelectorAll("a")].find((a) => a.getAttribute("href") === "/liked");
    expect(link).toBeDefined();
    expect(link!.textContent?.trim()).toBe("Liked articles");
  });

  it("has no Muted sources section, and keeps the Sources and topics link (#407)", async () => {
    installRouter({ online: false });
    const s = await loadStores();
    const hidden = await import("../feedback/hidden");
    await hidden.muteSource(KEPT);
    await showSettings();
    await vi.waitFor(() => expect(root.textContent).toContain("Sources and topics"));
    await settle();

    expect(root.textContent).not.toMatch(/muted sources/i);
    expect(root.textContent).not.toMatch(/no sources muted/i);
    expect(buttonsNamed(root, /unmute/i)).toEqual([]);
    expect(root.textContent).not.toContain(KEPT.domain);
    const link = [...root.querySelectorAll("a")].find(
      (a) => a.getAttribute("href") === "/settings/preferences",
    );
    expect(link?.textContent?.trim()).toBe("Sources and topics");
    expect(await queuedEvents(s)).toHaveLength(1);
  });

  it("with queued feedback, Sign out shows #184's warning and clears nothing before a choice", async () => {
    installRouter({ online: false });
    const s = await loadStores();
    const { startFeedback } = await import("../feedback/start");
    startFeedback();
    await s.enqueueFeedback(KEPT.id, "open");
    const queuedBefore = await queuedEvents(s);
    await showSettings();
    await vi.waitFor(() => expect(buttonsNamed(root, /sign out/i)).toHaveLength(1));

    buttonsNamed(root, /sign out/i)[0]!.click();

    await vi.waitFor(() => expect(document.querySelector("[role='alertdialog']")).not.toBeNull());
    await settle();
    expect(await queuedEvents(s)).toEqual(queuedBefore);
    expect(here.replace).not.toHaveBeenCalled();
  });

  it("shows the app version", async () => {
    installRouter({ online: false });
    await loadStores();
    await showSettings();
    await vi.waitFor(() => expect(root.textContent).toContain("9.8.7-invented"));
  });
});

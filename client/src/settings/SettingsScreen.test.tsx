// @vitest-environment happy-dom
//
// The Settings screen (#217) at /settings: muted sources with Unmute, Sign out through #184's
// guard, and the app version.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { unmuteButtonFor } from "../../test/cards";
import {
  buttonsNamed,
  installRouter,
  loadStores,
  locationAt,
  makeItem,
  queuedEvents,
  settle,
  type Stores,
} from "../../test/test-env";

const GONE = makeItem("a0b1c2d3e4f5a6b7", {
  title: "Invented story about rooftop beehives",
  source_id: "invented-hives",
  domain: "hives.example.test",
});
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

async function loadWithMutes(): Promise<{ s: Stores; hidden: typeof import("../feedback/hidden") }> {
  installRouter({ online: false });
  const s = await loadStores();
  const hidden = await import("../feedback/hidden");
  await hidden.muteSource(GONE);
  await hidden.muteSource(KEPT);
  return { s, hidden };
}

describe("SettingsScreen", () => {
  it("lists muted sources by domain, and Unmute queues mute_source 0 for an item no longer in the feed", async () => {
    const { s, hidden } = await loadWithMutes();
    await showSettings();
    await vi.waitFor(() => expect(unmuteButtonFor(root, GONE.domain)).toBeDefined());
    expect(unmuteButtonFor(root, KEPT.domain)).toBeDefined();

    unmuteButtonFor(root, GONE.domain)!.click();

    await vi.waitFor(() => expect(root.textContent).not.toContain(GONE.domain));
    expect(root.textContent).toContain(KEPT.domain);
    const later = makeItem("c2d3e4f5a6b7c8d9", { source_id: GONE.source_id, domain: GONE.domain });
    expect(await hidden.isHidden(later)).toBe(false);
    const last = (await queuedEvents(s)).at(-1)!;
    expect(last).toMatchObject({ article_id: GONE.id, event: "mute_source", value: 0 });
  });

  it("with queued feedback, Sign out shows #184's warning and clears nothing before a choice", async () => {
    const { s, hidden } = await loadWithMutes();
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
    expect((await hidden.listMuted()).map((i) => i.id).sort()).toEqual([GONE.id, KEPT.id].sort());
    expect(here.replace).not.toHaveBeenCalled();
  });

  it("shows the app version", async () => {
    installRouter({ online: false });
    await loadStores();
    await showSettings();
    await vi.waitFor(() => expect(root.textContent).toContain("9.8.7-invented"));
  });
});

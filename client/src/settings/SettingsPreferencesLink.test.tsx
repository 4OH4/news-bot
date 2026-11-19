// @vitest-environment happy-dom
//
// #338: Settings links to the preferences screen.
import { h, render } from "preact";
import { afterEach, expect, it, vi } from "vitest";
import { installRouter, loadStores, locationAt, settle } from "../../test/test-env";

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("Settings has a 'Sources and topics' link to /settings/preferences", async () => {
  vi.stubGlobal("location", locationAt("/settings"));
  vi.stubGlobal("__APP_VERSION__", "test");
  installRouter({ online: false });
  await loadStores();
  const { SettingsScreen } = await import("./SettingsScreen");
  const root = document.body.appendChild(document.createElement("div"));
  render(h(SettingsScreen, null), root);
  await settle();
  const link = [...root.querySelectorAll("a")].find((a) => a.textContent?.trim() === "Sources and topics");
  expect(link?.getAttribute("href")).toBe("/settings/preferences");
});

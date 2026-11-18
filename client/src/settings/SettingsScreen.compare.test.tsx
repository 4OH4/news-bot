// @vitest-environment happy-dom
//
// The Compare ranking group in Settings (#328).
import { h, render } from "preact";
import { afterEach, expect, it, vi } from "vitest";
import { installRouter, loadStores } from "../../test/test-env";

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it.each([
  ["Feed, rules only", "/?ranking=rules"],
  ["Feed, personalised only", "/?ranking=personal"],
])("links '%s' to %s", async (name, href) => {
  vi.stubGlobal("__APP_VERSION__", "9.8.7-invented");
  installRouter({ online: false });
  await loadStores();
  const root = document.body.appendChild(document.createElement("div"));
  const { SettingsScreen } = await import("./SettingsScreen");
  render(h(SettingsScreen, null), root);
  const link = [...root.querySelectorAll("a")].find((a) => a.textContent?.trim() === name);
  expect(link?.getAttribute("href")).toBe(href);
  expect(root.textContent).toContain("Compare ranking");
  render(null, root);
});

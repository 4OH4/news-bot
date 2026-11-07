import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const headers: Record<string, string> = JSON.parse(
  readFileSync(new URL("../security-headers.json", import.meta.url), "utf8"),
);

function directive(name: string): string | undefined {
  const csp = headers["Content-Security-Policy"] ?? "";
  return csp
    .split(";")
    .map((d) => d.trim())
    .find((d) => d.startsWith(`${name} `))
    ?.slice(name.length + 1);
}

describe("security-headers.json", () => {
  it("allows scripts only from our own origin", () => {
    expect(directive("script-src")).toBe("'self'");
  });

  it("allows connections only to our own origin", () => {
    expect(directive("connect-src")).toBe("'self'");
  });

  it("sets the other headers the API sets", () => {
    expect(headers["Referrer-Policy"]).toBe("no-referrer");
    expect(headers["X-Content-Type-Options"]).toBe("nosniff");
  });
});

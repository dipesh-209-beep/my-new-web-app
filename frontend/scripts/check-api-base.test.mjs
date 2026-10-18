import { describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Spawns the real script in a child process: it calls process.exit(), so it
// cannot be imported. Mirrors how `npm run build:production` invokes it.
const script = join(dirname(fileURLToPath(import.meta.url)), "check-api-base.mjs");

function run(env) {
  const childEnv = {
    ...process.env,
    NEXT_PUBLIC_API_BASE_URL: "",
    ALLOW_DEFAULT_API_BASE: "",
    ALLOW_LOCAL_API_BASE: "",
    ...env,
  };
  try {
    const stdout = execFileSync(process.execPath, [script], {
      env: childEnv,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { code: 0, stdout };
  } catch (err) {
    const e = err;
    return { code: e.status ?? 1, stdout: e.stdout ?? "", stderr: e.stderr ?? "" };
  }
}

describe("check-api-base", () => {
  it("refuses to build when NEXT_PUBLIC_API_BASE_URL is unset", () => {
    const { code, stderr } = run({});
    expect(code).toBe(1);
    expect(stderr).toContain("NEXT_PUBLIC_API_BASE_URL is not set");
  });

  it("accepts an explicit absolute https base", () => {
    const { code, stdout } = run({ NEXT_PUBLIC_API_BASE_URL: "https://api.example.com" });
    expect(code).toBe(0);
    expect(stdout).toContain("API base = https://api.example.com");
  });

  it("rejects a loopback base unless explicitly allowed", () => {
    expect(run({ NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000" }).code).toBe(1);
    expect(
      run({ NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000", ALLOW_LOCAL_API_BASE: "1" }).code,
    ).toBe(0);
  });

  it("rejects a malformed base", () => {
    expect(run({ NEXT_PUBLIC_API_BASE_URL: "not-a-url" }).code).toBe(1);
  });

  it("allows the :8000 fallback only when ALLOW_DEFAULT_API_BASE=1", () => {
    expect(run({ ALLOW_DEFAULT_API_BASE: "1" }).code).toBe(0);
  });
});

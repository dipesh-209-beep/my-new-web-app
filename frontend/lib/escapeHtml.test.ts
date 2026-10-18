import { describe, expect, it } from "vitest";
import { escapeHtml } from "@/lib/escapeHtml";

describe("escapeHtml", () => {
  it("escapes ampersands (AUD-008 acceptance)", () => {
    expect(escapeHtml("a&b")).toBe("a&amp;b");
    expect(escapeHtml("A & B")).toBe("A &amp; B");
  });

  it("escapes the other HTML-significant characters", () => {
    expect(escapeHtml("<script>")).toBe("&lt;script&gt;");
    expect(escapeHtml('say "hi"')).toBe("say &quot;hi&quot;");
    expect(escapeHtml("it's")).toBe("it&apos;s");
  });

  it("escapes & before < and > so entities aren't double-built", () => {
    expect(escapeHtml("<a & b>")).toBe("&lt;a &amp; b&gt;");
  });

  it("neutralizes entity-encoded markup", () => {
    const out = escapeHtml("&#60;img src=x onerror=alert(1)&#62;");
    // The leading & is escaped, so re-decoding yields literal text, never a tag.
    expect(out).not.toContain("<img");
    expect(out.startsWith("&amp;#60;")).toBe(true);
  });

  it("leaves a name with no special characters unchanged", () => {
    expect(escapeHtml("Ratnapark → Koteshwor")).toBe("Ratnapark → Koteshwor");
  });
});

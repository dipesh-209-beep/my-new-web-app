import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, findRoute, getAllStops, getRouteGeometry, getRouteStops, getStops } from "@/lib/api";
import { Stop } from "@/types/route";

function jsonResponse(body: unknown, init: { status?: number; ok?: boolean } = {}) {
  const status = init.status ?? 200;
  return {
    ok: init.ok ?? (status >= 200 && status < 300),
    status,
    json: async () => body,
  } as Response;
}

function makeStop(id: string): Stop {
  return {
    stop_id: id,
    stop_name: `Stop ${id}`,
    lat: 27.7,
    lng: 85.3,
    zone: null,
    district: null,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
  };
}

describe("lib/api request()", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resolves with the parsed JSON body on a 2xx response", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ total: 1, limit: 50, offset: 0, items: [makeStop("S0001")] })
    );
    const result = await getStops();
    expect(result.total).toBe(1);
    expect(result.items[0].stop_id).toBe("S0001");
  });

  it("builds the query string from provided params, omitting null/undefined", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ total: 0, limit: 50, offset: 0, items: [] })
    );
    await getStops({ limit: 10, offset: 0, district: undefined });
    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toContain("limit=10");
    expect(calledUrl).toContain("offset=0");
    expect(calledUrl).not.toContain("district");
  });

  it("throws an ApiError with kind 'http' and the backend's detail message on a non-2xx response", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ detail: "Stop not found." }, { status: 404, ok: false })
    );
    await expect(getStops()).rejects.toMatchObject({
      name: "ApiError",
      kind: "http",
      status: 404,
      message: "Stop not found.",
    });
  });

  it("falls back to a generic message when the error body has no detail field", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({}, { status: 500, ok: false })
    );
    await expect(getStops()).rejects.toMatchObject({
      kind: "http",
      status: 500,
      message: "Request failed (500).",
    });
  });

  it("throws an ApiError with kind 'network' when fetch itself rejects", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(getStops()).rejects.toMatchObject({ kind: "network" });
  });

  it("throws an ApiError with kind 'parse' when the response body isn't valid JSON", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    } as unknown as Response);
    await expect(getStops()).rejects.toMatchObject({ kind: "parse" });
  });
});

describe("getAllStops pagination", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns every stop in one page when total fits under the page size", async () => {
    const items = [makeStop("S0001"), makeStop("S0002")];
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ total: 2, limit: 100, offset: 0, items })
    );
    const all = await getAllStops();
    expect(all.map((s) => s.stop_id)).toEqual(["S0001", "S0002"]);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("fires the remaining pages in parallel once the first page reveals the total, preserving order", async () => {
    const total = 250; // pageSize is 100 internally -> pages at offset 0, 100, 200
    const fetchMock = fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (url: string) => {
      const offset = Number(new URL(url).searchParams.get("offset"));
      const pageItems = Array.from({ length: Math.min(100, total - offset) }, (_, i) =>
        makeStop(`S${String(offset + i).padStart(4, "0")}`)
      );
      return jsonResponse({ total, limit: 100, offset, items: pageItems });
    });

    const all = await getAllStops();
    expect(all).toHaveLength(total);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(all[0].stop_id).toBe("S0000");
    expect(all[all.length - 1].stop_id).toBe(`S${String(total - 1).padStart(4, "0")}`);
  });
});

describe("findRoute 404 handling", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resolves { found: false } on a 404, rather than throwing", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ detail: "No route found." }, { status: 404, ok: false })
    );
    const outcome = await findRoute({ origin: "S0001", destination: "S0002" });
    expect(outcome).toEqual({ found: false });
  });

  it("resolves { found: true, result } on success", async () => {
    const result = {
      origin_stop_id: "S0001",
      destination_stop_id: "S0002",
      total_cost: 12,
      transfer_count: 0,
      legs: [],
      fare: null,
      alternatives: [],
    };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse(result));
    const outcome = await findRoute({ origin: "S0001", destination: "S0002" });
    expect(outcome).toEqual({ found: true, result });
  });

  it("still throws for a non-404 error status (500 is a real failure, not 'no route')", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ detail: "Internal error." }, { status: 500, ok: false })
    );
    await expect(findRoute({ origin: "S0001", destination: "S0002" })).rejects.toBeInstanceOf(
      ApiError
    );
  });
});

describe("getRouteStops / getRouteGeometry direction param", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to direction=forward when not specified", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse([]));
    await getRouteStops("R1");
    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toContain("/routes/R1/stops");
    expect(calledUrl).toContain("direction=forward");
  });

  it("passes direction=reverse through to the request URL", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ geometry: { type: "LineString", coordinates: [] }, distance_m: 0, duration_s: 0 })
    );
    await getRouteGeometry("R1", "reverse");
    const calledUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(calledUrl).toContain("/routes/R1/geometry");
    expect(calledUrl).toContain("direction=reverse");
  });
});

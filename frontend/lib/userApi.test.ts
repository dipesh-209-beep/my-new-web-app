import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  clearUserSession,
  clearUserToken,
  getUsername,
  getUserToken,
  setUsername,
  setUserToken,
  userListSuggestions,
  userLogin,
  userRegister,
  userSubmitSuggestion,
} from "@/lib/userApi";
import { Suggestion, UserTokenResponse } from "@/types/route";

function jsonResponse(body: unknown, init: { status?: number } = {}) {
  const status = init.status ?? 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function makeSuggestion(overrides: Partial<Suggestion> = {}): Suggestion {
  return {
    suggestion_id: 1,
    target_type: "stop",
    target_id: "S0001",
    suggestion_type: "stop_name_change",
    payload: { stop_name: "Better Name" },
    status: "pending",
    vote_count: 1,
    created_at: "2026-09-09T11:59:36.319409+00:00",
    submitted_by: "alice",
    reviewed_by: null,
    voted_by_me: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("userApi credentials", () => {
  it("stores and reads the user token and username separately from admin tokens", () => {
    setUserToken("user-jwt");
    setUsername("alice");
    expect(getUserToken()).toBe("user-jwt");
    expect(getUsername()).toBe("alice");
    expect(window.localStorage.getItem("ktm-transit:admin-token")).toBeNull();
  });

  it("clears both token and username together", () => {
    setUserToken("user-jwt");
    setUsername("alice");
    clearUserSession();
    expect(getUserToken()).toBeNull();
    expect(getUsername()).toBeNull();
  });

  it("only clears the token on clearUserToken", () => {
    setUserToken("user-jwt");
    setUsername("alice");
    clearUserToken();
    expect(getUserToken()).toBeNull();
    expect(getUsername()).toBe("alice");
  });
});

describe("userRegister / userLogin", () => {
  it("registers a user and returns the access token", async () => {
    const tokenResp: UserTokenResponse = { access_token: "jwt-1", token_type: "bearer" };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse(tokenResp, { status: 201 }));

    const result = await userRegister("alice", "secret");

    expect(result).toEqual(tokenResp);
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ username: "alice", password: "secret" });
  });

  it("logs a user in against /auth/login", async () => {
    const tokenResp: UserTokenResponse = { access_token: "jwt-2", token_type: "bearer" };
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse(tokenResp));

    const result = await userLogin("alice", "secret");

    expect(result.access_token).toBe("jwt-2");
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain("/auth/login");
  });
});

describe("userSubmitSuggestion", () => {
  it("POSTs the suggestion with the bearer token and parses the response", async () => {
    const suggestion = makeSuggestion({ vote_count: 1 });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse(suggestion, { status: 201 }));

    const result = await userSubmitSuggestion(
      {
        target_type: "stop",
        target_id: "S0001",
        suggestion_type: "stop_name_change",
        payload: { stop_name: "Better Name" },
      },
      "jwt-1",
    );

    expect(result).toEqual(suggestion);
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain("/suggestions");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer jwt-1");
    expect(JSON.parse(init.body)).toEqual({
      target_type: "stop",
      target_id: "S0001",
      suggestion_type: "stop_name_change",
      payload: { stop_name: "Better Name" },
    });
  });

  it("throws an ApiError carrying the backend detail for a 409", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ detail: "You already submitted this exact suggestion." }, { status: 409 }),
    );

    const err = await userSubmitSuggestion(
      {
        target_type: "stop",
        target_id: "S0001",
        suggestion_type: "stop_name_change",
        payload: { stop_name: "Better Name" },
      },
      "jwt-1",
    ).then(
      () => null,
      (e) => e,
    );

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).message).toBe("You already submitted this exact suggestion.");
  });
});

describe("userListSuggestions", () => {
  it("GETs the pending-suggestion list", async () => {
    const list = [makeSuggestion()];
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse(list));

    const result = await userListSuggestions(null);

    expect(result).toHaveLength(1);
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain("/suggestions");
    expect(init.method).toBe("GET");
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("attaches the bearer token when one is supplied (for voted_by_me)", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse([]));

    await userListSuggestions("jwt-1");

    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer jwt-1");
  });
});
/**
 * lib/userApi.ts
 *
 * Authenticated client for the public-user endpoints (backend/app/api/auth.py +
 * app/api/suggestions.py): registration, login, and proposing changes to
 * stops/routes. Mirrors the admin-token pattern in lib/adminApi.ts — the token
 * lives in localStorage under a distinct key (`ktm-transit:user-token`) so a
 * user session survives page reloads, and admin-role tokens stay fully
 * separate (different storage key, different JWT audience on the backend).
 *
 * Endpoints wrapped (matched exactly — no invented routes):
 *   POST /auth/register
 *   POST /auth/login
 *   POST /suggestions
 *   GET  /suggestions
 */

import { ApiError } from "@/lib/api";
import { apiBase } from "@/lib/apiBase";
import { Suggestion, SuggestionCreatePayload, UserTokenResponse } from "@/types/route";

const USER_TOKEN_KEY = "ktm-transit:user-token";
const USER_NAME_KEY = "ktm-transit:user-name";

export function getUserToken(): string | null {
  try {
    return window.localStorage.getItem(USER_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setUserToken(token: string): void {
  try {
    window.localStorage.setItem(USER_TOKEN_KEY, token);
  } catch {
    // Storage unavailable (private browsing / quota) — the session just
    // won't survive a reload, which is acceptable for a suggestion box.
  }
}

export function clearUserToken(): void {
  try {
    window.localStorage.removeItem(USER_TOKEN_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** Display name for the signed-in user, kept alongside the token. */
export function getUsername(): string | null {
  try {
    return window.localStorage.getItem(USER_NAME_KEY);
  } catch {
    return null;
  }
}

export function setUsername(username: string): void {
  try {
    window.localStorage.setItem(USER_NAME_KEY, username);
  } catch {
    /* storage unavailable — token itself still works for this tab */
  }
}

function clearUsername(): void {
  try {
    window.localStorage.removeItem(USER_NAME_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** Wipe a user session (token + cached username). */
export function clearUserSession(): void {
  clearUserToken();
  clearUsername();
}

/**
 * Registers a callback fired whenever an authenticated request comes back
 * 401 (expired/revoked JWT). The UserAuthContext wires this to its `logout`
 * so the UI drops the stale session immediately instead of showing the
 * user as signed-in until the next manual action. Mirrors lib/adminApi's
 * per-form `onForbidden` handling, but automatically for every call here.
 */
export function onUserTokenExpired(callback: () => void): void {
  _onTokenExpired = callback;
}

let _onTokenExpired: (() => void) | null = null;

const DEFAULT_TIMEOUT_MS = 10_000;

async function userRequest<T>(
  method: "GET" | "POST",
  path: string,
  body: unknown,
  token: string | null,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${apiBase()}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    if (controller.signal.aborted) {
      throw new ApiError("Request timed out.", "timeout");
    }
    throw new ApiError("Couldn't reach the server. Check your connection.", "network");
  } finally {
    clearTimeout(timeout);
  }

  // Expired/revoked session: wipe the stored token and tell the context to
  // update, then surface a 401 just like the caller expects. Detection is
  // limited to requests that actually carried a token -- unauthenticated
  // endpoints (login/register) legitimately 401 without any session to
  // clear.
  if (res.status === 401 && token) {
    clearUserSession();
    _onTokenExpired?.();
    throw new ApiError("Session expired. Please sign in again.", "http", 401);
  }

  if (!res.ok) {
    let detail = "";
    try {
      const bodyJson = await res.json();
      detail = typeof bodyJson?.detail === "string" ? bodyJson.detail : "";
    } catch {
      /* body wasn't JSON */
    }
    throw new ApiError(detail || `Request failed (${res.status}).`, "http", res.status);
  }

  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("Received an invalid response from the server.", "parse");
  }
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export function userRegister(username: string, password: string): Promise<UserTokenResponse> {
  return userRequest<UserTokenResponse>("POST", "/auth/register", { username, password }, null);
}

export function userLogin(username: string, password: string): Promise<UserTokenResponse> {
  return userRequest<UserTokenResponse>("POST", "/auth/login", { username, password }, null);
}

// ---------------------------------------------------------------------------
// Suggestions
// ---------------------------------------------------------------------------

/**
 * Submit a change suggestion — or, if an identical change is already pending
 * for this target, vote for it (the backend answers 201 for a new suggestion,
 * 200 for a vote; both carry the suggestion payload in the body).
 */
export function userSubmitSuggestion(
  body: SuggestionCreatePayload,
  token: string,
): Promise<Suggestion> {
  return userRequest<Suggestion>("POST", "/suggestions", body, token);
}

/** Public listing of pending suggestions; pass a token to get `voted_by_me`. */
export function userListSuggestions(token: string | null = null): Promise<Suggestion[]> {
  return userRequest<Suggestion[]>("GET", "/suggestions", undefined, token);
}
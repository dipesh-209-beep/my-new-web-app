"use client";

import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  clearUserSession,
  getUsername,
  getUserToken,
  onUserTokenExpired,
  setUsername,
  setUserToken,
} from "@/lib/userApi";

interface UserAuthValue {
  /** JWT for the signed-in user, or null when anonymous. */
  token: string | null;
  /** Stored display name of the signed-in user. */
  username: string | null;
  /** False until the localStorage session has been read (avoids hydration
   * flashes where the NavBar briefly shows "Sign in" for a returning user). */
  hydrated: boolean;
  login: (token: string, username: string) => void;
  logout: () => void;
}

const UserAuthContext = createContext<UserAuthValue | null>(null);

/**
 * Holds the signed-in public-user session (from lib/userApi.ts storage) and
 * keeps NavBar + suggestion forms in sync. The provider is mounted in
 * app/layout.tsx. While it hydrates (first effect tick) `token` is null and
 * `hydrated` is false — children that render auth-dependent UI should wait
 * for `hydrated` so a returning user isn't flashed the logged-out state.
 */
export function UserAuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [username, setUserName] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    // Deferred to a timeout (same trick as NavBar's own hydration) so the
    // session restore reads localStorage after mount and never triggers a
    // hydration-mismatch render.
    const timer = setTimeout(() => {
      setToken(getUserToken());
      setUserName(getUsername());
      setHydrated(true);
    }, 0);
    return () => clearTimeout(timer);
  }, []);

  const login = useCallback((newToken: string, newUsername: string) => {
    setUserToken(newToken);
    setUsername(newUsername);
    setToken(newToken);
    setUserName(newUsername);
  }, []);

  const logout = useCallback(() => {
    clearUserSession();
    setToken(null);
    setUserName(null);
  }, []);

  // Any authenticated userApi call that comes back 401 (expired/revoked
  // JWT) triggers the same logout the user would get from clicking "Sign
  // out" -- otherwise the session lingers in localStorage and the NavBar
  // keeps showing "signed in" while every suggestion submission fails.
  useEffect(() => {
    onUserTokenExpired(logout);
  }, [logout]);

  const value = useMemo(
    () => ({ token, username, hydrated, login, logout }),
    [token, username, hydrated, login, logout],
  );

  return <UserAuthContext.Provider value={value}>{children}</UserAuthContext.Provider>;
}

export function useUserAuth(): UserAuthValue {
  const ctx = useContext(UserAuthContext);
  if (!ctx) {
    throw new Error("useUserAuth must be used within a UserAuthProvider.");
  }
  return ctx;
}
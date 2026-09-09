"use client";

import { FormEvent, useState } from "react";
import { userLogin, userRegister } from "@/lib/userApi";
import { ApiError } from "@/lib/api";
import InlineAlert from "@/components/ui/InlineAlert";
import { useUserAuth } from "@/components/user/UserAuthContext";

interface UserLoginProps {
  /** Called after a successful login/registration (handy for closing a
   * popover); the session itself is already stored via the auth context. */
  onDone?: () => void;
}

/** Sign-in / create-account form for public users (POST /auth/login and
 * POST /auth/register). Mirrors AdminLogin's styling; registration is
 * included because suggestion features need it. */
export default function UserLogin({ onDone }: UserLoginProps) {
  const { login } = useUserAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function switchMode(next: "login" | "register") {
    setMode(next);
    setError(null);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("Enter a username and password.");
      return;
    }
    if (mode === "register" && password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result =
        mode === "login"
          ? await userLogin(username.trim(), password)
          : await userRegister(username.trim(), password);
      login(result.access_token, username.trim());
      onDone?.();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(err.message || "That username is already taken.");
      } else if (err instanceof ApiError && err.status === 401) {
        setError(err.message || "Invalid username or password.");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Couldn't reach the server. Check your connection.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full rounded-xl border border-route-line bg-surface-raised p-4 shadow-card"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold tracking-tight text-ink">
          {mode === "login" ? "Sign in" : "Create an account"}
        </h2>
        <div className="flex rounded-lg bg-surface-sunken p-0.5 text-xs" role="group" aria-label="Choose action">
          <button
            type="button"
            onClick={() => switchMode("login")}
            aria-pressed={mode === "login"}
            className={`rounded-md px-2.5 py-1 font-medium transition-colors ${
              mode === "login" ? "bg-white text-ink shadow-sm" : "text-ink-secondary hover:text-ink"
            }`}
          >
            Sign in
          </button>
          <button
            type="button"
            onClick={() => switchMode("register")}
            aria-pressed={mode === "register"}
            className={`rounded-md px-2.5 py-1 font-medium transition-colors ${
              mode === "register" ? "bg-white text-ink shadow-sm" : "text-ink-secondary hover:text-ink"
            }`}
          >
            Register
          </button>
        </div>
      </div>

      <div className="mt-3 flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-ink-secondary">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
        {mode === "register" && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-ink-secondary">Confirm password</span>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              required
              className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
            />
          </label>
        )}
      </div>

      {error && (
        <div className="mt-3">
          <InlineAlert variant="error">{error}</InlineAlert>
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="mt-4 w-full rounded-md bg-brand px-3 py-2 text-sm font-semibold text-ink transition-colors hover:bg-brand-dark disabled:opacity-50"
      >
        {submitting ? (mode === "login" ? "Signing in…" : "Creating account…") : mode === "login" ? "Sign in" : "Create account"}
      </button>
    </form>
  );
}
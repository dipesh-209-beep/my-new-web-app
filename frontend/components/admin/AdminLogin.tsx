"use client";

import { FormEvent, useState } from "react";
import { adminLogin } from "@/lib/adminApi";
import { ApiError } from "@/lib/api";
import InlineAlert from "@/components/ui/InlineAlert";

interface AdminLoginProps {
  onLogin: (token: string) => void;
}

/**
 * Login form for POST /admin/login. The admin API also accepts an
 * X-Admin-Api-Key header (used by scripts/ETL), but a browser can't carry
 * a shared secret header safely, so the UI uses the per-admin JWT flow:
 * the token is stored in localStorage (see lib/adminApi.ts) and sent as
 * `Authorization: Bearer <token>` on every admin call.
 */
export default function AdminLogin({ onLogin }: AdminLoginProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("Enter a username and password.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const { access_token } = await adminLogin(username.trim(), password);
      onLogin(access_token);
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 401
          ? "Invalid username or password."
          : err instanceof ApiError
            ? err.message
            : "Couldn't reach the server. Check your connection.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full max-w-sm rounded-xl border border-route-line bg-surface-raised p-5 shadow-card"
    >
      <h2 className="text-lg font-semibold tracking-tight text-ink">Admin sign in</h2>
      <p className="mt-1 text-sm text-ink-secondary">
        The admin API is also reachable by script with an <code>X-Admin-Api-Key</code>;
        this form uses the per-admin login (JWT).
      </p>

      <div className="mt-4 flex flex-col gap-3">
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
            autoComplete="current-password"
            required
            className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue"
          />
        </label>
      </div>

      {error && <div className="mt-3"><InlineAlert variant="error">{error}</InlineAlert></div>}

      <button
        type="submit"
        disabled={submitting}
        className="mt-4 w-full rounded-md bg-brand px-3 py-2 text-sm font-semibold text-ink transition-colors hover:bg-brand-dark disabled:opacity-50"
      >
        {submitting ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
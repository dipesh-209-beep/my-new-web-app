"use client";

import { useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { userListSuggestions, userSubmitSuggestion } from "@/lib/userApi";
import InlineAlert from "@/components/ui/InlineAlert";
import UserLogin from "@/components/user/UserLogin";
import { useUserAuth } from "@/components/user/UserAuthContext";
import { RouteStopEntry, Suggestion } from "@/types/route";

interface SuggestionBoxProps {
  targetType: "stop" | "route";
  targetId: string;
  /** Route detail pages pass their stops (in ride order) so users can also
   * suggest a stop reorder. Hidden when omitted. */
  currentStops?: RouteStopEntry[];
}

/** Lets signed-in visitors propose changes to a stop or route (see
 * backend/app/api/suggestions.py). Shows pending change suggestions already
 * on file for this target, with a "back it" vote button; identical repeats of
 * a pending suggestion are exactly how vote counts grow. */
export default function SuggestionBox({ targetType, targetId, currentStops }: SuggestionBoxProps) {
  const { token, hydrated } = useUserAuth();

  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [suggestionsError, setSuggestionsError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  // Drag-free reorder: the order state is the target's stop_ids arranged the
  // way the user wants them; the payload maps back to current sequence_no
  // values (which is what the backend expects — see apply_stop_sequence_change).
  const sig = currentStops?.map((e) => `${e.stop.stop_id}:${e.sequence_no}`).join("|") ?? "";
  const [order, setOrder] = useState<string[]>(
    () => currentStops?.map((e) => e.stop.stop_id) ?? [],
  );
  const seqById = useMemo(() => {
    const map: Record<string, number> = {};
    for (const e of currentStops ?? []) map[e.stop.stop_id] = e.sequence_no;
    return map;
  }, [currentStops]);

  // When the route's stops change (initial load, forward/reverse toggle),
  // fall back to the natural ride order. Done at render time rather than in
  // an effect so it can't race a user's in-progress reorder.
  const [lastSig, setLastSig] = useState(sig);
  if (sig !== lastSig) {
    setLastSig(sig);
    setOrder(currentStops?.map((e) => e.stop.stop_id) ?? []);
  }

  async function reloadSuggestions() {
    try {
      const all = await userListSuggestions(token);
      setSuggestions(
        all.filter(
          (s) => s.target_type === targetType && s.target_id === targetId && s.status === "pending",
        ),
      );
      setSuggestionsError(null);
    } catch {
      setSuggestionsError("Couldn't load existing suggestions.");
    }
  }

  useEffect(() => {
    if (!hydrated) return;
    let cancelled = false;

    async function load() {
      try {
        const all = await userListSuggestions(token);
        if (cancelled) return;
        setSuggestions(
          all.filter(
            (s) => s.target_type === targetType && s.target_id === targetId && s.status === "pending",
          ),
        );
        setSuggestionsError(null);
      } catch {
        if (!cancelled) setSuggestionsError("Couldn't load existing suggestions.");
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [hydrated, token, targetType, targetId]);

  function postSubmit(suggestion: Suggestion) {
    if (suggestion.status === "auto_applied") {
      setMessage({ kind: "success", text: "Enough people backed this — it's already been applied to the live data." });
    } else if (suggestion.vote_count <= 1) {
      setMessage({ kind: "success", text: "Suggestion submitted. It'll be applied once enough people back it." });
    } else {
      setMessage({ kind: "success", text: `Vote counted — this suggestion now has ${suggestion.vote_count} backing.` });
    }
    setName("");
    reloadSuggestions();
  }

  function handleSubmitError(err: unknown) {
    if (err instanceof ApiError) {
      setMessage({ kind: "error", text: err.message || `Request failed (${err.status}).` });
    } else {
      setMessage({ kind: "error", text: "Couldn't reach the server. Check your connection." });
    }
  }

  async function submitNameChange() {
    if (!token || !name.trim()) return;
    setSubmitting(true);
    setMessage(null);
    try {
      const suggestion = await userSubmitSuggestion(
        {
          target_type: targetType,
          target_id: targetId,
          suggestion_type: targetType === "stop" ? "stop_name_change" : "route_name_change",
          payload:
            targetType === "stop" ? { stop_name: name.trim() } : { route_name: name.trim() },
        },
        token,
      );
      postSubmit(suggestion);
    } catch (err) {
      handleSubmitError(err);
    } finally {
      setSubmitting(false);
    }
  }

  async function submitReorder() {
    if (!token || order.length === 0) return;
    const sequence = order.map((id) => seqById[id]).filter((s) => s !== undefined);
    if (sequence.length === 0) return;
    setSubmitting(true);
    setMessage(null);
    try {
      const suggestion = await userSubmitSuggestion(
        {
          target_type: "route",
          target_id: targetId,
          suggestion_type: "stop_sequence_change",
          payload: { sequence },
        },
        token,
      );
      postSubmit(suggestion);
    } catch (err) {
      handleSubmitError(err);
    } finally {
      setSubmitting(false);
    }
  }

  async function vote(suggestion: Suggestion) {
    if (!token) return;
    setSubmitting(true);
    setMessage(null);
    try {
      const updated = await userSubmitSuggestion(
        {
          target_type: suggestion.target_type,
          target_id: suggestion.target_id,
          suggestion_type: suggestion.suggestion_type,
          payload: suggestion.payload,
        },
        token,
      );
      postSubmit(updated);
    } catch (err) {
      handleSubmitError(err);
    } finally {
      setSubmitting(false);
    }
  }

  function move(kind: "up" | "down", index: number) {
    setOrder((prev) => {
      const next = [...prev];
      const target = kind === "up" ? index - 1 : index + 1;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  const seqPayload = order.map((id) => seqById[id]).filter((s) => s !== undefined);
  const naturalSeq = [...seqPayload].sort((a, b) => a - b);
  const reorderChanged = JSON.stringify(seqPayload) !== JSON.stringify(naturalSeq);

  if (!hydrated) return null;

  return (
    <section className="rounded-xl border border-route-line bg-surface-raised p-4 shadow-card">
      <h2 className="text-sm font-semibold tracking-tight text-ink">Suggest a change</h2>

      {!token && (
        <div className="mt-3 flex flex-col gap-3">
          <p className="text-sm text-ink-secondary">
            Everyone can help keep the route data accurate. Sign in to suggest a change or back one.
          </p>
          <UserLogin />
        </div>
      )}

      {token && (
        <div className="mt-3 flex flex-col gap-4">
          {targetType === "stop" ? (
            <p className="text-sm text-ink-secondary">
              Spotted a better name for this stop? Put it forward — identical suggestions from other users count as votes.
            </p>
          ) : (
            <p className="text-sm text-ink-secondary">
              Spotted a better route name, or a stop that&apos;s out of order? Put it forward below.
            </p>
          )}

          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-2">
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-xs font-medium text-ink-secondary">
                  Suggest {targetType === "stop" ? "a stop name" : "a route name"}
                </span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={
                    targetType === "stop" ? "New stop name…" : "New route name…"
                  }
                  disabled={submitting}
                  className="rounded-md border border-route-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent-blue disabled:opacity-60"
                />
              </label>
              <button
                type="button"
                onClick={submitNameChange}
                disabled={submitting || !name.trim()}
                className="self-start rounded-md bg-brand px-3 py-1.5 text-sm font-semibold text-ink transition-colors hover:bg-brand-dark disabled:opacity-50"
              >
                {submitting ? "Submitting…" : "Suggest name"}
              </button>
            </div>

            {targetType === "route" && currentStops && currentStops.length > 1 && (
              <div className="flex flex-col gap-2">
                <span className="text-xs font-medium text-ink-secondary">Suggest a stop reorder</span>
                <ol className="flex flex-col">
                  {order.map((stopId, i) => {
                    const entry = currentStops.find((e) => e.stop.stop_id === stopId);
                    if (!entry) return null;
                    return (
                      <li key={stopId} className="flex items-center gap-2 py-1 text-sm text-ink">
                        <span className="w-4 shrink-0 font-mono text-xs text-ink-secondary">{entry.sequence_no}</span>
                        <span className="min-w-0 flex-1 truncate">{entry.stop.stop_name}</span>
                        <span className="flex shrink-0 gap-1">
                          <button
                            type="button"
                            onClick={() => move("up", i)}
                            disabled={i === 0 || submitting}
                            aria-label={`Move ${entry.stop.stop_name} up`}
                            className="rounded border border-route-line px-1.5 py-0.5 text-xs text-ink-secondary hover:border-accent-blue hover:text-ink disabled:opacity-40"
                          >
                            ↑
                          </button>
                          <button
                            type="button"
                            onClick={() => move("down", i)}
                            disabled={i === order.length - 1 || submitting}
                            aria-label={`Move ${entry.stop.stop_name} down`}
                            className="rounded border border-route-line px-1.5 py-0.5 text-xs text-ink-secondary hover:border-accent-blue hover:text-ink disabled:opacity-40"
                          >
                            ↓
                          </button>
                        </span>
                      </li>
                    );
                  })}
                </ol>
                <button
                  type="button"
                  onClick={submitReorder}
                  disabled={submitting || !reorderChanged || seqPayload.length === 0}
                  className="self-start rounded-md border border-route-line px-3 py-1.5 text-sm text-ink-secondary hover:border-accent-blue hover:text-ink disabled:opacity-50"
                >
                  {submitting ? "Submitting…" : "Suggest this order"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {message && (
        <div className="mt-3">
          <InlineAlert variant={message.kind === "success" ? "warning" : "error"}>{message.text}</InlineAlert>
        </div>
      )}

      {suggestionsError ? (
        <div className="mt-3">
          <InlineAlert variant="warning" action={{ label: "Retry", onClick: reloadSuggestions }}>
            {suggestionsError}
          </InlineAlert>
        </div>
      ) : suggestions.length > 0 ? (
        <div className="mt-3 flex flex-col gap-2">
          <span className="text-xs font-medium text-ink-secondary">Pending changes for this {targetType}</span>
          {suggestions.map((s) => (
            <div
              key={s.suggestion_id}
              className="flex items-center justify-between gap-3 rounded-lg border border-route-line bg-white px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-sm text-ink">
                  {s.suggestion_type === "stop_name_change" && (
                    <>Rename to <span className="font-medium">{String(s.payload?.stop_name)}</span></>
                  )}
                  {s.suggestion_type === "route_name_change" && (
                    <>Rename route to <span className="font-medium">{String(s.payload?.route_name)}</span></>
                  )}
                  {s.suggestion_type === "stop_sequence_change" && (
                    <>Reorder route stops (new sequence: {(s.payload?.sequence as number[] | undefined)?.join(", ")})</>
                  )}
                </p>
                <p className="mt-0.5 text-xs text-ink-secondary">
                  by {s.submitted_by ?? "a user"} · {s.vote_count} vote{s.vote_count === 1 ? "" : "s"}
                </p>
              </div>
              {token && (
                <button
                  type="button"
                  onClick={() => vote(s)}
                  disabled={submitting || s.voted_by_me === true}
                  className={`shrink-0 rounded-md px-3 py-1 text-sm font-medium transition-colors ${
                    s.voted_by_me === true
                      ? "border border-accent-green/40 bg-accent-green/10 text-accent-green"
                      : "border border-route-line text-ink-secondary hover:border-accent-green hover:text-accent-green"
                  } disabled:opacity-60`}
                >
                  {s.voted_by_me === true ? "Backed" : "Back this"}
                </button>
              )}
            </div>
          ))}
        </div>
      ) : token ? (
        <p className="mt-3 text-sm text-ink-secondary">No pending suggestions for this {targetType} yet.</p>
      ) : null}
    </section>
  );
}
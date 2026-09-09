"""Business logic shared by the suggestion write paths.

Both writers -- a suggestion hitting AUTO_APPLY_VOTE_THRESHOLD
(app/api/suggestions.py::create_suggestion) and an editor/admin approving
one (app/api/suggestions.py::review_suggestion) -- apply the payload to the
live dataset through the single function here, so the two can never drift
apart. It makes *mutations only* and does NOT commit: the caller is
typically committing a compound transaction (status flip + review audit
trail + the applied change) and needs that atomicity.
"""

from sqlalchemy.orm import Session

from app.db.queries import apply_stop_sequence_change
from app.models import Route, RouteSuggestion, Stop


class TargetMissingError(Exception):
    """The stop/route a suggestion targets no longer exists (or, for a
    sequence change, the order it proposes no longer matches the route's
    current stops). Callers surface this as a 409: nothing was applied,
    and the suggestion stays pending."""


def apply_suggestion(session: Session, suggestion: RouteSuggestion) -> None:
    """Apply a suggestion's payload to the live dataset.

    Mutates ORM objects in `session` (and, for stop_sequence_change, runs
    the structural reorder via app/db/queries.py::apply_stop_sequence_change
    including graph-version bump + total_stops sync). Does NOT commit --
    the caller owns the transaction. Raises TargetMissingError if the
    target has disappeared or the proposed sequence no longer applies, in
    which case the caller should roll back and return 409.

    The suggestion's own status is NOT touched here -- callers set it
    (approved / auto_applied) before calling so it commits atomically."""
    target = suggestion.target_type
    target_id = suggestion.target_id
    stype = suggestion.suggestion_type
    payload = suggestion.payload

    if target == "stop":
        if stype != "stop_name_change":
            raise TargetMissingError(
                f"suggestion_type '{stype}' does not apply to a stop target."
            )
        stop = session.get(Stop, target_id)
        if stop is None:
            raise TargetMissingError(f"Stop '{target_id}' no longer exists (suggestion can't be applied).")
        stop.stop_name = payload["stop_name"]
        return

    if stype == "route_name_change":
        route = session.get(Route, target_id)
        if route is None:
            raise TargetMissingError(f"Route '{target_id}' no longer exists (suggestion can't be applied).")
        route.route_name = payload["route_name"]
        return

    if stype == "stop_sequence_change":
        # apply_stop_sequence_change validates the proposed order is still a
        # permutation of the route's CURRENT stops and raises ValueError
        # otherwise (it may have changed since the suggestion was accepted).
        try:
            apply_stop_sequence_change(session, target_id, payload["sequence"])
        except ValueError as exc:
            raise TargetMissingError(str(exc)) from exc
        return

    raise TargetMissingError(f"Unknown suggestion_type '{stype}' for a route target.")
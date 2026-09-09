"""Crowd-sourced route/stop edit suggestions.

Public paths:
  POST /suggestions       -- logged-in user proposes a change (or, if an
                             identical change is already pending, this
                             counts as their vote). 10/hour per client IP.
                             Auto-applies at AUTO_APPLY_VOTE_THRESHOLD.
  GET  /suggestions       -- public view of pending suggestions, ordered by
                             support; accepts an optional user token to mark
                             which ones the caller already backed.

Admin paths (require_admin/require_role(_EDIT), same as app/api/admin.py):
  GET   /admin/suggestions -- list by status (default pending), for review.
  PATCH /admin/suggestions/{id} -- approve (applies the change) or reject.
                             Only valid while the suggestion is 'pending'
                             (409 after auto-apply wins the race).

The dedup-as-vote rule (two people submitting the same change shouldn't
create two rows) is enforced by the partial unique index on
route_suggestions(target_type, target_id, suggestion_type, payload_hash)
WHERE status = 'pending' (see models/route_suggestion.py) plus the lookup
below -- payload_hash is sha256 of the sorted-JSON of the raw payload, so
"exactly the same change" is exact, ordering-insensitive keys and all.
"""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.response_cache import invalidate as invalidate_cache
from app.core.security import (
    ROLE_ADMIN,
    ROLE_EDITOR,
    get_current_user,
    get_current_user_optional,
    require_role,
)
from app.db.session import get_db
from app.models import AdminUser, Route, RouteSuggestion, Stop, SuggestionVote, User
from app.schemas import SuggestionAction, SuggestionCreate, SuggestionOut
from app.services.suggestions import TargetMissingError, apply_suggestion

router = APIRouter(tags=["suggestions"])

# Same gate as admin.py's _EDIT (editor and admin both review suggestions;
# other data-entry routes are gated narrowly there, review of a
# crowd-sourced change is the same class of routine fix as create_stop).
_EDIT = Depends(require_role(ROLE_EDITOR, ROLE_ADMIN))

# Namespaces a stop_sequence_change write touches (see admin.py for the
# per-namespace rationale -- this mirrors its _ROUTE_STOP_CACHE_KEYS).
_SEQUENCE_CACHE_KEYS = ("routes", "route_detail", "route_stops", "route_geometry", "stops")


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _invalidate_after_apply(suggestion: RouteSuggestion) -> None:
    """Re-key the response caches a rendered suggestion changes. Only call
    after the change was actually committed -- a pending suggestion changes
    nothing, so its vote shouldn't nuke caches."""
    if suggestion.suggestion_type == "stop_name_change":
        invalidate_cache("stops")
        invalidate_cache("route_stops")
    elif suggestion.suggestion_type == "route_name_change":
        invalidate_cache("routes")
        invalidate_cache("route_detail")
    elif suggestion.suggestion_type == "stop_sequence_change":
        for key in _SEQUENCE_CACHE_KEYS:
            invalidate_cache(key)


def _suggestion_out(
    suggestion: RouteSuggestion, db: Session, votes_user_id: int | None = None
) -> SuggestionOut:
    out = SuggestionOut(
        suggestion_id=suggestion.suggestion_id,
        target_type=suggestion.target_type,
        target_id=suggestion.target_id,
        suggestion_type=suggestion.suggestion_type,
        payload=suggestion.payload,
        status=suggestion.status,
        vote_count=suggestion.vote_count,
        created_at=suggestion.created_at.isoformat(),
    )
    if suggestion.user_id is not None:
        author = db.get(User, suggestion.user_id)
        out.submitted_by = author.username if author is not None else None
    if suggestion.reviewed_by is not None:
        reviewer = db.get(AdminUser, suggestion.reviewed_by)
        out.reviewed_by = reviewer.username if reviewer is not None else None
    if votes_user_id is not None:
        out.voted_by_me = (
            db.scalar(
                select(SuggestionVote).where(
                    SuggestionVote.suggestion_id == suggestion.suggestion_id,
                    SuggestionVote.user_id == votes_user_id,
                )
            )
            is not None
        )
    return out


def _require_target_exists(db: Session, payload: SuggestionCreate) -> None:
    if payload.target_type == "stop":
        if db.get(Stop, payload.target_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Stop '{payload.target_id}' not found.")
    else:
        if db.get(Route, payload.target_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Route '{payload.target_id}' not found.")


@router.post("/suggestions", response_model=SuggestionOut)
@limiter.limit("10/hour")
def create_suggestion(
    request: Request,
    payload: SuggestionCreate,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SuggestionOut:
    """Submit a change suggestion -- or, if an identical change is already
    pending, cast a vote for it. Returns 201 for a new suggestion, 200 for
    a vote on an existing one."""
    _require_target_exists(db, payload)

    raw_payload = payload.payload
    payload_hash = _payload_hash(raw_payload)

    existing = db.scalar(
        select(RouteSuggestion).where(
            RouteSuggestion.target_type == payload.target_type,
            RouteSuggestion.target_id == payload.target_id,
            RouteSuggestion.suggestion_type == payload.suggestion_type,
            RouteSuggestion.payload_hash == payload_hash,
            RouteSuggestion.status == "pending",
        )
    )
    if existing is not None:
        if existing.user_id == user.user_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You already submitted this exact suggestion.")
        if (
            db.scalar(
                select(SuggestionVote).where(
                    SuggestionVote.suggestion_id == existing.suggestion_id,
                    SuggestionVote.user_id == user.user_id,
                )
            )
            is not None
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You already voted for this suggestion.")

        db.add(SuggestionVote(suggestion_id=existing.suggestion_id, user_id=user.user_id))
        existing.vote_count += 1
        suggestion = existing
        response.status_code = status.HTTP_200_OK
    else:
        suggestion = RouteSuggestion(
            user_id=user.user_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            suggestion_type=payload.suggestion_type,
            payload=raw_payload,
            payload_hash=payload_hash,
            status="pending",
            vote_count=1,
        )
        db.add(suggestion)
        db.flush()  # get suggestion_id for the author's implicit vote row
        db.add(SuggestionVote(suggestion_id=suggestion.suggestion_id, user_id=user.user_id))
        response.status_code = status.HTTP_201_CREATED

    applied = False
    threshold = get_settings().AUTO_APPLY_VOTE_THRESHOLD
    if suggestion.status == "pending" and suggestion.vote_count >= threshold:
        # Auto-apply: this request pushed the suggestion over the line. Status
        # flip + data change + audit timestamp commit atomically below -- if
        # the change can't be applied the whole vote/suggestion rolls back.
        suggestion.status = "auto_applied"
        suggestion.reviewed_at = datetime.now(timezone.utc)
        applied = True
        try:
            apply_suggestion(db, suggestion)
        except TargetMissingError as exc:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    db.commit()
    if applied:
        _invalidate_after_apply(suggestion)
    db.refresh(suggestion)
    return _suggestion_out(suggestion, db, votes_user_id=user.user_id)


@router.get("/suggestions", response_model=list[SuggestionOut])
def list_suggestions(
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> list[SuggestionOut]:
    """Public listing of pending suggestions, most-supported first. Optional
    auth lets the frontend mark which ones the current user already backed."""
    rows = db.scalars(
        select(RouteSuggestion)
        .where(RouteSuggestion.status == "pending")
        .order_by(RouteSuggestion.vote_count.desc(), RouteSuggestion.suggestion_id.desc())
    ).all()
    votes_user_id = current_user.user_id if current_user is not None else None
    return [_suggestion_out(s, db, votes_user_id=votes_user_id) for s in rows]


@router.get("/admin/suggestions", response_model=list[SuggestionOut], dependencies=[_EDIT])
def admin_list_suggestions(
    suffix_status: str = Query("pending", alias="status"),
    db: Session = Depends(get_db),
) -> list[SuggestionOut]:
    """Admin review queue, filterable by status ('pending' | 'approved' |
    'rejected' | 'auto_applied')."""
    allowed = {"pending", "approved", "rejected", "auto_applied"}
    if suffix_status not in allowed:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(allowed)}.")
    rows = db.scalars(
        select(RouteSuggestion)
        .where(RouteSuggestion.status == suffix_status)
        .order_by(RouteSuggestion.vote_count.desc(), RouteSuggestion.suggestion_id.desc())
    ).all()
    return [_suggestion_out(s, db) for s in rows]


@router.patch("/admin/suggestions/{suggestion_id}", response_model=SuggestionOut, dependencies=[_EDIT])
def review_suggestion(
    suggestion_id: int,
    payload: SuggestionAction,
    _admin: AdminUser | None = Depends(require_role(ROLE_EDITOR, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> SuggestionOut:
    """Approve (apply now, bypassing further votes) or reject a pending
    suggestion. Only 'pending' suggestions can be reviewed -- once a
    suggestion auto-applies or is reviewed, further review is a 409."""
    suggestion = db.get(RouteSuggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Suggestion {suggestion_id} not found.")
    if suggestion.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Suggestion is no longer pending (currently '{suggestion.status}').",
        )

    suggestion.reviewed_by = _admin.admin_id if _admin is not None else None
    suggestion.reviewed_at = datetime.now(timezone.utc)

    applied = False
    if payload.action == "approve":
        suggestion.status = "approved"
        applied = True
        try:
            apply_suggestion(db, suggestion)
        except TargetMissingError as exc:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    else:
        suggestion.status = "rejected"

    db.commit()
    if applied:
        _invalidate_after_apply(suggestion)
    db.refresh(suggestion)
    return _suggestion_out(suggestion, db)
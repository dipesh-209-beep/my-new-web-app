# Privacy Notice

**Last updated: 2026-09-29**

This describes what the Kathmandu Bus Route Finder collects, why, and how
long it is kept. It covers the software as configured in this repository,
not any particular future deployment of it — an operator running their own
instance may configure logging, retention and analytics differently, and
would be responsible for telling their own users about that.

## What we collect

**The routing features are public: searching routes, viewing stops on the
map, and walking directions need no account and no personal data.**

Submitting a community correction to a stop or route is *not* one of those
features: it requires signing up. `POST /suggestions` depends on
`get_current_user`, so there is no way to propose or back a change
anonymously. Reading suggestions is public — `GET /suggestions` takes an
*optional* user token and works without one.

The personal data this system can hold therefore comes from three sources:
what a member of the public chooses to enter when registering a public
account, what administrators choose to type in, and network addresses in
logs.

| What | Where it lives | Why |
|---|---|---|
| Public user account: username, password hash, registration time | `users` table | Signing in to submit and vote on suggestions. The password is stored as a salted hash, never in plaintext. No email address, real name, or phone number is requested or stored. |
| Public user JWTs | Issued to the client, held in the browser's `localStorage` under `ktm-transit:user-token`; not stored server-side | Session identity. Stateless; the server only holds the signing key. `localStorage` is readable by any script running on the page, unlike an `HttpOnly` cookie. The chosen username is cached alongside it under `ktm-transit:user-name`. |
| Suggestions | `route_suggestions` table | Accepting community corrections to stops and routes. Each row is linked to the submitting account via `user_id`, and stores the proposed new stop/route name or stop ordering as submitted. |
| Votes | `suggestion_votes` table | Counting support for a suggestion. Each vote is linked to both the suggestion and the voting account; one account can back a given suggestion at most once. |
| Admin account username and password hash | `admin_users` table | Authenticating administrators. The password is stored as a salted hash, never in plaintext. |
| Admin JWTs | Issued to the client, held in the browser's `localStorage` under `ktm-transit:admin-token`; not stored server-side | Session identity. Stateless; the server only holds the signing key. |
| Service credentials | `service_credentials` table | Authenticating unattended callers such as ETL scripts. Only a SHA-256 hash of the secret is stored; the plaintext is shown once at creation and never again. |
| Admin actions | `admin_audit_log` table | Recording who changed which stop or route, when, and from where. See below. |
| Request metadata | Container/host logs only | Debugging. See [Logs](#logs) below. |

There is no analytics SDK, no third-party tracking, no advertising, and no
cookie set by the application. Session tokens live in `localStorage`
instead — see the caveat above.

### Your username is public

`GET /suggestions` is unauthenticated, and each returned suggestion
includes a `submitted_by` field holding the submitting account's **username
in cleartext**. Anyone who can reach the API can therefore read the
usernames of everyone with a **pending** suggestion, together with the
content and timing of what they submitted. (The public endpoint filters to
`status = "pending"`; once a suggestion is auto-applied, approved or
rejected it drops out of that listing, and its author's username is then
reachable only through the admin-only `GET /admin/suggestions`.) Choose a
username you are content to publish. Nothing else about the account — the
password hash, or the account's votes beyond the aggregate `vote_count` — is
exposed this way.

## Suggestions, votes, and automatic application

A suggestion is created on a user's first submission. If a signed-in user
submits a change identical to one that is still pending, it is recorded as
a **vote** on the existing suggestion rather than a new row; identical
pending submissions therefore deduplicate instead of piling up. The
author's own submission counts as their first vote.

Once a pending suggestion reaches **26 votes from distinct accounts**
(`AUTO_APPLY_VOTE_THRESHOLD` in `backend/app/core/config.py`), the change
is applied to the live stop or route data automatically, with the
suggestion's status set to `auto_applied`. No administrator reviews this
path. The account that submits the 26th vote triggers the change. An
administrator can also approve or reject a pending suggestion by hand, in
which case the decision *is* recorded in `admin_audit_log`.

## Audit records

`admin_audit_log` stores, for administrative changes: the acting principal
type and id, the action taken, the resource type and id that were changed,
the request ID, the client's IP address, and a redacted summary of the
change. It never stores passwords, JWTs, or the plaintext of a
service-credential secret.

Two details that are easy to get wrong:

- **The principal is a service credential's `key_id`, not its name.** The
  human-readable name lives only inside the `detail` text on the events that
  create or revoke a credential. The three principal types are
  `admin_user`, `service_credential`, and `anonymous`; there is no `system`
  actor. `anonymous` (a null actor id) is what a failed login or a rejected
  request is recorded as. When a change arrives via the legacy shared key,
  the actor is recorded as the literal `shared-api-key`.
- **The HTTP method and path are recorded only for security events**
  (failed logins, authorization failures), not for successful changes. A
  change row records *what* changed, not the endpoint it was made through.

The IP address is the reason to read the rest of this section: it is
personal data under GDPR and equivalent laws, and it is recorded
unconditionally, because attributing a destructive change to a specific
connection is the point of the log.

**Retention: no automatic expiry is currently implemented.** Rows are kept
until an operator deletes them. If you deploy this, either add a retention
job or accept unbounded growth knowingly — see
[Known Gaps](#known-gaps).

Audit records are written by the application, which has no delete path for
them. They are not, however, tamper-proof: anyone with direct write access
to the database can alter or remove them. They are an accountability
record, not an immutable log.

## Logs

nginx and the backend write access logs to the container's stdout, which
whatever log driver the operator runs captures. The backend's
`TRUST_PROXY_HEADERS` setting determines whether the recorded address is the
client's or the proxy's; it is only correct to enable when the proxy is the
sole path in, because otherwise a client can forge `X-Forwarded-For` and
have arbitrary addresses written into the log.

Container logs are not automatically deleted by anything in this
repository. Configure your driver's rotation.

## Third parties and subprocessors

- **PostgreSQL/PostGIS and Redis** run locally in the Compose stack. Neither
  leaves the host.
- **OSRM** (the road-geometry service) receives only coordinates. It is a
  routing engine with no persistence, not a data processor in any useful
  sense.
- **OpenStreetMap tile servers** receive every map tile request: the tile
  URL and the client's IP address. This is the one place where using the app
  causes an external disclosure, and it is worth knowing before deploying.
  Note that the provider is **hardcoded** to
  `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png` in
  `frontend/components/map/useMap.ts` — there is no setting for it, so every
  deployment of this repository discloses to OpenStreetMap unless the source
  is edited. OSM's [tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
  prohibits bulk use of the public tile servers; check your deployment's
  traffic against it.
- **No other outbound requests** are made by the backend or frontend.

## Your rights

Where the GDPR or an equivalent law applies, a person can ask for access to,
correction of, or deletion of their personal data, and can object to
processing. In practice:

- **Suggestion, vote, and account data is linked to a signed-in account**,
  so there *is* an identity to match a request against — but only in the
  sense of the username you chose. The system holds no email address or
  other contact detail for a public account, so locating a request from
  outside the system is not possible. A request should go to the operator
  of the instance, who can look rows up by username.
- **There is no self-service account deletion.** No endpoint lets a person
  delete their own account or withdraw their suggestions; the only way out
  is for the operator to act. What deleting a `users` row actually does is
  defined by the schema: the account's `suggestion_votes` rows are deleted
  with it (cascade), but the suggestions it authored **survive** with their
  `user_id` set to `NULL` — and because `submitted_by` is derived from that
  column, an anonymised suggestion is one whose `submitted_by` reads
  `null`. Note that `vote_count` is a denormalized column incremented on
  each vote and never recalculated, so it does **not** fall when votes are
  cascade-deleted; see
  [Known Gaps](#known-gaps).
- **Admin and audit data** is held by the operator of the instance. A
  request should go to them; this repository has no contact address because
  it is software, not a service.
- Deleting an admin account does not delete that account's audit rows, which
  are retained deliberately so the record of a change outlives the person
  who made it.
- **The usernames in the `users` table are public** (see
  [Your username is public](#your-username-is-public)). This is a
  disclosure, not a rights failure, but it is worth knowing before
  registering.

## Children

This is a public transport information tool. It is not directed at children.
It collects no personal data from anyone, of any age, *except* where
someone registers a public account to submit a suggestion, which requires a
username and a password and is therefore unlikely to be a child. No age is
requested, and no age verification exists, so nothing here should be read
as a safeguard.

## Security

Authentication, authorization and auditing are described in
[README.md](README.md#authentication-and-authorization). In short:
administrator JWTs and scoped, revocable service credentials; a legacy
shared key that is refused at startup in production; per-IP rate limiting on
both admin login and public user registration/login; and an audit trail for
administrative changes.

Public user and administrator accounts are entirely separate: a user token
cannot satisfy an admin permission check, and a user account can never reach
the admin API.

No system is perfectly secure. Report a vulnerability to the operator of
your instance rather than in a public issue tracker.

## Known Gaps

- **No retention job.** Audit rows, public accounts, suggestions, votes and
  logs are kept indefinitely unless an operator adds expiry. No application
  code path ever deletes a `users` row — there is no delete endpoint, and no
  scheduled job. This is the most significant gap here.
- **IP addresses are recorded without notice to the person being recorded.**
  There is no privacy notice served by the application itself, because
  there is no frontend route that serves one. The rate limiter on
  registration and login also stores client IP addresses — as counter keys,
  in Redis when `REDIS_URL` is set (`RATE_LIMIT_REDIS_URL` overrides it),
  otherwise in per-worker memory. Those counters expire with their window;
  nothing in this repository purges them explicitly.
- **Automatic application of a public vote to a suggestion is not audited.**
  Only the manual review decision is. The suggestion row records that it
  became `auto_applied` and when, but the resulting stop or route change is
  not written to `admin_audit_log` at all, and `reviewed_by` is left unset
  because no human reviewed it. The 26th vote is attributable to a
  `users.user_id` in `suggestion_votes`, but nothing ties the change itself
  to that account.
- **`vote_count` is denormalized and never recalculated.** It is incremented
  when a vote is cast and left alone otherwise, so it can overstate support
  if votes are removed — including by the cascade that fires when an account
  is deleted, and by any manual database edit. A suggestion can therefore
  hold a `vote_count` of 26 that no longer corresponds to 26 existing
  accounts. It is also the only thing consulted against the auto-apply
  threshold, so an inflated count can cause a change to be applied.
- **No withdrawal.** A person cannot retract a vote or a suggestion, and an
  applied change cannot be reverted through the API.
- **No cookie banner**, because no cookies are set. If an operator adds
  analytics or session cookies, this notice stops being accurate and must be
  updated. Likewise, session tokens are in `localStorage` rather than an
  `HttpOnly` cookie, so they are exposed to any script running on the page.

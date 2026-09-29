# Privacy Notice

**Last updated: 2026-09-27**

This describes what the Kathmandu Bus Route Finder collects, why, and how
long it is kept. It covers the software as configured in this repository,
not any particular future deployment of it — an operator running their own
instance may configure logging, retention and analytics differently, and
would be responsible for telling their own users about that.

## What we collect

**Almost nothing about you.** The routing features are public: searching
routes, viewing stops on the map, and walking directions need no account and
no personal data.

The only personal data this system can hold is what administrators and
senders of the public feedback form choose to type in:

| What | Where it lives | Why |
|---|---|---|
| Admin account username and password hash | `admin_users` table | Authenticating administrators. The password is stored as a salted hash, never in plaintext. |
| Admin JWTs | Issued to the client, not stored server-side | Session identity. Stateless; the server only holds the signing key. |
| Service credentials | `service_credentials` table | Authenticating unattended callers such as ETL scripts. Only a SHA-256 hash of the secret is stored; the plaintext is shown once at creation and never again. |
| Admin actions | `admin_audit_log` table | Recording who changed which stop or route, when, and from where. See below. |
| Public suggestions and votes | `suggestions`, `suggestion_votes` tables | Accepting community corrections to stops and routes. No account is required, so these are not linked to an identity. |
| Request metadata | Container/host logs only | Debugging. See [Logs](#logs) below. |

There is no analytics SDK, no third-party tracking, no advertising, and no
cookie set by the application.

## Audit records

`admin_audit_log` stores, for administrative changes: the acting principal
(admin username, service credential name, or `"system"`), the HTTP method
and path, the request ID, the client's IP address, and a redacted summary
of the change. It never stores passwords, JWTs, or the plaintext of a
service-credential secret.

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
- **Leaflet** loads map tiles from a tile server configured by the operator.
  A tile request discloses the tile coordinates and the client's IP address
  to that third party. This is the one place where using the app causes an
  external disclosure, and it is worth knowing before deploying: check
  which tile provider your instance uses and what its terms are.
- **No other outbound requests** are made by the backend or frontend.

## Your rights

Where the GDPR or an equivalent law applies, a person can ask for access to,
correction of, or deletion of their personal data, and can object to
processing. In practice:

- **Suggestion and vote data** is submitted without an account, so there is
  no identity to match a request against. Requests about it should be sent
  to the operator, who can act on the submitted content.
- **Admin and audit data** is held by the operator of the instance. A
  request should go to them; this repository has no contact address because
  it is software, not a service.
- Deleting an admin account does not delete that account's audit rows, which
  are retained deliberately so the record of a change outlives the person
  who made it.

## Children

This is a public transport information tool. It is not directed at children
and collects no personal data from anyone, of any age, without an
administrator or a member of the public choosing to enter something.

## Security

Authentication, authorization and auditing are described in
[README.md](README.md#authentication-and-authorization). In short:
administrator JWTs and scoped, revocable service credentials; a legacy
shared key that is refused at startup in production; per-IP rate limiting on
admin login; and an audit trail for administrative changes.

No system is perfectly secure. Report a vulnerability to the operator of
your instance rather than in a public issue tracker.

## Known Gaps

- **No retention job.** Audit rows and logs are kept indefinitely unless an
  operator adds expiry. This is the most significant gap here.
- **IP addresses are recorded without notice to the person being recorded.**
  There is no privacy notice served by the application itself, because
  there is no frontend route that serves one.
- **Automatic application of a public vote to a suggestion is not audited.**
  Only the manual review decision is. The vote that triggered it is
  attributable to a submission, not to a person, but the resulting stop or
  route change is not recorded in `admin_audit_log` at all.
- **No cookie banner**, because no cookies are set. If an operator adds
  analytics or session cookies, this notice stops being accurate and must be
  updated.

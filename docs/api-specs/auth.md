# API Spec: Auth Endpoints

Added in V5.6.0 (`feature/cloud-deploy`). Provides bcrypt-based password auth with versioned bearer tokens.

All other API endpoints require `Authorization: Bearer <token>` (via `BearerTokenMiddleware`). SSE endpoints accept `?token=<token>` as an alternative.

---

## POST /auth/login

Authenticate with password, returns a short-lived bearer token.

**Request**
```json
{ "password": "string" }
```

**Response 200**
```json
{
  "token": "hex64string",
  "token_version": 1,
  "expires_in": 86400
}
```

**Response 401** — wrong password
```json
{ "detail": "Invalid credentials" }
```

**Notes**
- Token is derived from a bcrypt-hashed credential in `auth_credentials` DuckDB table (id=1).
- On first boot, `UIS_AUTH_TOKEN` env var seeds the initial password hash. After `change-password`, the env var is no longer valid for login (token_version incremented).

---

## POST /auth/change-password

Replace the current password. Invalidates all previously issued tokens.

**Request** (requires `Authorization: Bearer <token>`)
```json
{
  "current_password": "string",
  "new_password": "string"
}
```

**Response 200**
```json
{ "message": "Password changed successfully" }
```

**Response 401** — wrong current password
```json
{ "detail": "Invalid credentials" }
```

**Notes**
- Increments `token_version` — all tokens issued before the change are rejected.
- Minimum password length: 8 characters.

---

## POST /auth/validate

Check whether the current bearer token is valid. Used by the frontend on page load.

**Request** (requires `Authorization: Bearer <token>`)

No body.

**Response 200**
```json
{ "valid": true, "token_version": 1 }
```

**Response 401**
```json
{ "detail": "Unauthorized" }
```

---

## POST /auth/logout-all

Invalidate all active tokens (increments token_version without changing password).

**Request** (requires `Authorization: Bearer <token>`)

No body.

**Response 200**
```json
{ "message": "All sessions invalidated" }
```

---

## BearerTokenMiddleware exemptions

The following paths are exempt from authentication:
- `POST /auth/login` — login must be unauthenticated
- `GET /health` — health check endpoint
- Requests with `OPTIONS` method (CORS preflight)
- Static file requests in local dev mode (no `UIS_AUTH_TOKEN` set)

SSE paths (`/sync/stream`, `/ai-advisor/analyze`) accept `?token=<token>` query param in addition to the `Authorization` header (EventSource API cannot set headers).

---

## Schema

```sql
CREATE TABLE auth_credentials (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    password_hash VARCHAR NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

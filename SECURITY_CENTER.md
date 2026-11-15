# Database Security Center

The **Security Center** is available from the sidebar at `http://localhost:8000/` after signing in with an administrator account. Backend checks are session protected and repeat the admin-role check on every management endpoint.

## What the current deployment verifies

At implementation time the running MongoDB server was 7.0.45, replica set `rs0`. Read-only server inspection reported authorization disabled, TLS disabled, and an unauthenticated application-to-MongoDB connection. The Security Center therefore reports native MongoDB user/role creation as unavailable, marks TLS as not configured, and does not claim that encrypted connectivity or plaintext rejection was tested. Platform account authentication is a separate application feature and does not enable MongoDB server authorization.

The page can manage existing platform accounts using the existing `subscription_users` records and only the existing `user` / `admin` application roles. It excludes password hashes and salts, prevents demoting the final administrator, and writes a credential-free entry to `security_audit` for each role change. MongoDB users/roles are managed separately and only become available after the optional native-admin secret is configured and MongoDB authorization is verified as enabled.

The current database has no administrator account. To initialize access, an operator can explicitly promote one existing account once; the command refuses if an administrator already exists and writes an audit record:

```powershell
docker exec <backend-container> python -m backend.bootstrap_security_admin --email "existing-account@example.com" --confirm
```

This command does not create accounts or change passwords. Do not run it unless the selected existing account belongs to the intended platform administrator.

## Secure native MongoDB management configuration

Set the following values in the backend's secret configuration. The administrator connection string should use a dedicated user with user/role management permissions limited to the databases in the allowlist. Do not use an ordinary application account or paste credentials into frontend code, command history, logs, or Git.

```text
MONGO_SECURITY_ADMIN_URI_FILE=/run/secrets/mongo-security-admin-uri
MONGO_SECURITY_DATABASES=chapter07
```

The mounted file named by `MONGO_SECURITY_ADMIN_URI_FILE` contains one MongoDB URI. Example format (replace the placeholders in the secret file; URI-encode special characters in the password):

```text
mongodb://security-manager:<encoded-password>@fresh-mongo1:27017,fresh-mongo2:27017,fresh-mongo3:27017/admin?replicaSet=rs0&authSource=admin
```

The backend reads the secret file at process start. Restarting the existing container does not change its environment or mounts. A deployment update must recreate only the backend container while preserving its existing image/network/port configuration, app environment, and Lab 9 results mount. Keep the old backend container for rollback. Do not enable database authorization by merely adding this secret: the MongoDB members must first be configured for authorization with the correct replica-set internal keyfile and a known administrative account.

`MONGO_SECURITY_DATABASES` is a comma-separated allowlist; it defaults to `MONGO_DATABASE`. The UI only lets the native-admin connection manage databases on this list and collections that already exist. The server presets are fixed:

- **MongoDB read-only:** `find` on one database and collection.
- **MongoDB restricted writer:** `find`, `insert`, and `update` on one database and collection. It does not grant `delete`, collection creation, index changes, or database administration.

The permission test's `find` returns only an identifier. Its `update` uses MongoDB `explain` and does not execute a write. MongoDB authorization is the enforcement boundary; the platform UI is not used as a substitute for server privileges.

## TLS certificates and backend connection

The backend supports these mounted file settings:

```text
MONGO_TLS_CA_FILE=/run/secrets/mongo-ca.pem
MONGO_TLS_CLIENT_CERT_FILE=/run/secrets/mongo-client.pem
MONGO_TLS_CERTIFICATE_KEY_FILE=/run/secrets/mongo-server.pem
```

The CA file and optional client certificate are passed into the backend MongoDB client. The server certificate-key path is inspected for readability and expiration metadata only; the private key is never returned. The backend URI must request TLS, for example `mongodb://fresh-mongo1:27017,fresh-mongo2:27017,fresh-mongo3:27017/?replicaSet=rs0&tls=true`. Keep normal certificate validation enabled.

A basic OpenSSL workflow is shown in the Security Center. The server certificate needs Subject Alternative Names for every hostname clients and replica-set members use. Keep the CA private key offline. Combine the server certificate and private key in the server's configured `tlsCertificateKeyFile`; mount the resulting file read-only and restrict its permissions. Client certificates are optional unless mutual TLS is required.

MongoDB must be configured on every replica-set member, not only in the backend. The server settings are equivalent to:

```text
--tlsMode requireTLS
--tlsCertificateKeyFile /run/secrets/mongo-server.pem
--tlsCAFile /run/secrets/mongo-ca.pem
```

Apply replica-set security changes using a planned, member-by-member deployment procedure. This project does not change MongoDB command lines, certificates, replica-set membership, or credentials. With `requireTLS`, MongoDB rejects plaintext connections. The dashboard only reports that rejection verified after its TLS ping succeeds and its separate read-only plaintext ping is rejected during TLS negotiation.

## Security endpoints

All endpoints below require an administrator application session:

- `GET /api/security/overview` — verified auth/TLS/availability facts, application role, and configured allowlist.
- `GET /api/security/application-users` and `PATCH /api/security/application-users/{user_id}/role` — list accounts and change an application role.
- `GET /api/security/mongo/catalog`, `/mongo/users`, `/mongo/roles` — inspect the allowlisted native MongoDB catalog.
- `POST /api/security/mongo/roles`, `/mongo/users` — create fixed-scope roles and users.
- `POST /api/security/permissions/test` — safe `find` or no-write `update` permission test.
- `POST /api/security/views/redacted?database=...&collection=...` — create or verify a read-only view that excludes `salary`; source collections are not modified.
- `POST /api/security/tls/test` — verify a TLS-enabled backend ping and, in `requireTLS` mode, test plaintext rejection.
- `GET /api/security/audit` — latest credential-free security changes.

## Run and verify

```powershell
Set-Location 'C:\Users\hp\Downloads\nosql-lab-7'
docker restart fresh-backend
Invoke-RestMethod http://localhost:8000/health
Start-Process http://localhost:8000/account
```

Sign in as an application administrator, open **Security Center → Database Security**, and use **Refresh checks**. Until the native prerequisites are configured, role/user creation, permission tests, and redacted-view actions remain unavailable by design; the page reports those limits instead of showing a success state.

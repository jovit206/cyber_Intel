# Administrator access

The application stores privileged accounts in the existing `subscription_users` collection. `.env` remains excluded by `.gitignore`.

## Administrator login

Run from PowerShell after `fresh-backend` is running. The setup command reads `AEGIS_ADMIN_ID`, `AEGIS_ADMIN_PASSWORD`, and `AEGIS_ADMIN_EMAIL` from its process environment. It creates `admin` once, with a zero-balance wallet and no fabricated financial history. Re-running it does not reset the password or modify an existing admin. It refuses to promote an existing ordinary account.

```powershell
$env:AEGIS_ADMIN_ID = 'admin'
$env:AEGIS_ADMIN_PASSWORD = '12345'
$env:AEGIS_ADMIN_EMAIL = 'admin@aegis.local'
docker exec -e "AEGIS_ADMIN_ID=$env:AEGIS_ADMIN_ID" -e "AEGIS_ADMIN_PASSWORD=$env:AEGIS_ADMIN_PASSWORD" -e "AEGIS_ADMIN_EMAIL=$env:AEGIS_ADMIN_EMAIL" fresh-backend python -m backend.bootstrap_admin
Remove-Item Env:AEGIS_ADMIN_ID, Env:AEGIS_ADMIN_PASSWORD, Env:AEGIS_ADMIN_EMAIL
```

The password `12345` is only suitable for this local demonstration. Do not use it on a public deployment. For another environment, set a long random `AEGIS_ADMIN_PASSWORD` in that environment's protected secret store for the one-time setup command.

The local demonstration administrator has ID `admin` and password `12345` in the current `chapter07` database. The password is only suitable for this local demonstration. Passwords use the same salted PBKDF2-SHA256 hash as regular accounts. The bootstrap command stores only the hash and salt, and is idempotent: it neither changes an existing password nor promotes an existing user.

## Manager login

The read-only demonstration manager has ID `manager` and password `12345`. To bootstrap it in another environment, use process-scoped variables and clear them after setup:

```powershell
$env:AEGIS_MANAGER_ID = 'manager'
$env:AEGIS_MANAGER_PASSWORD = '12345'
$env:AEGIS_MANAGER_EMAIL = 'manager@aegis.local'
docker exec -e "AEGIS_MANAGER_ID=$env:AEGIS_MANAGER_ID" -e "AEGIS_MANAGER_PASSWORD=$env:AEGIS_MANAGER_PASSWORD" -e "AEGIS_MANAGER_EMAIL=$env:AEGIS_MANAGER_EMAIL" fresh-backend python -m backend.bootstrap_manager
Remove-Item Env:AEGIS_MANAGER_ID, Env:AEGIS_MANAGER_PASSWORD, Env:AEGIS_MANAGER_EMAIL
```

Sign in at `http://localhost:8000/account`. Admin and manager roles are redirected to separate dashboards. The Admin Dashboard navigation appears only for admins; the Manager Dashboard appears only for managers. Backend endpoints independently check the stored role. Manager sessions are read-only across all guarded APIs and receive HTTP 403 for writes. Manager access does not grant access to admin APIs.

The Admin Dashboard reads actual `subscription_users`, `wallets`, `wallet_transactions`, `wallet_operations`, `subscription_payments`, and `subscription_receipts` records. Lifetime financial totals come from canonical transaction rows plus unique failed receipts that have no committed operation. Active users are accounts with a successful `last_login_at` in the preceding 30 days; older activity is not inferred. Admins can ban/unban non-admin accounts and credit wallets with an amount, reason, and idempotency key. Credits use the existing atomic wallet service; balance, transaction, receipt, ledger, and audit writes commit together. Admin login/logout, failed privileged logins, ban/unban, and wallet credits are recorded in `admin_audit_log`. Manager views are read-only.

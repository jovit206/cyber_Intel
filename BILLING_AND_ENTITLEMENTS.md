# Wallet, subscriptions, and feature access

## Wallet and plan behavior

- Free wallets may hold up to **₹500**. New Free accounts start with the existing simulated opening allocation of ₹500. Deposits and incoming wallet transfers are checked inside the MongoDB transaction; existing balances above the cap are preserved and receive no additional funds until upgraded or brought below the limit.
- Plus is **₹299/month** and Pro is **₹799/month**. Existing simulated wallet transactions and subscription prices remain unchanged. Purchases require sufficient balance and update the wallet, plan, ledgers, receipt, and Banking Operations history atomically.
- Failed purchases create a rolled-back receipt visible in the user's financial history and do not change the wallet or plan. Idempotency keys prevent duplicate credits or charges.
- Banking Operations remains the single transaction-history view. Records are scoped to the signed-in user; the account profile does not duplicate transaction history.

## Entitlements

The backend policy is centralized in `backend/services/entitlements.py`. Free includes Live Threat Stream and Top Threats. Plus includes the supported premium features with monthly request limits; Search & Enrich is limited to three successful API operations per UTC calendar month. Pro has no monthly feature-use limit. Expired paid plans receive Free entitlements; no database migration or balance rewrite is performed.

Passive views that the dashboard loads or polls automatically (`GET /api/metrics`, `GET /api/lab72/analysis`, `GET /api/virustotal/recent`) require a plan that includes the feature but do not consume the monthly allowance; user-initiated operations such as searches, VirusTotal lookups, simulations and experiment runs are counted.

Default monthly Plus limits can be configured in the backend environment:

```text
PLUS_REPORTS_MONTHLY_LIMIT=30
PLUS_SEARCH_MONTHLY_LIMIT=3
PLUS_CROSS_SOURCE_MONTHLY_LIMIT=20
PLUS_WORKING_SET_MONTHLY_LIMIT=10
PLUS_WRITE_CONCERN_MONTHLY_LIMIT=5
PLUS_NETWORK_RELIABILITY_MONTHLY_LIMIT=5
PLUS_SHARDING_MONTHLY_LIMIT=10
PLUS_INVENTORY_MONTHLY_LIMIT=10
FREE_WALLET_LIMIT_INR=500
```

The entitlement middleware checks every mapped API request and atomically reserves limited monthly usage. It keeps the count only for successful responses and releases it on rejected or failed requests. Locking in the sidebar is informational; API enforcement is server-side. Monthly counters live in the separate `feature_usage` collection, keyed by account, feature, and UTC month.

## Account and payment screens

Click the platform name in the upper-left to open the account area. It shows profile details, the current plan, wallet balance, feature usage, and subscription choices. Add simulated wallet funds and review the canonical transaction history in Banking Operations. Deposits are simulated application-wallet credits; no real payment gateway is connected.

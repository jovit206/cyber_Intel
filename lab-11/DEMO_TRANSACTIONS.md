# Chapter 11.1 Transaction Dataset

The 1,400 persisted records in `chapter07.demo_financial_activity` are reserved exclusively for the Shard Key Evaluation. The old general-purpose demo history endpoint now returns HTTP 410, and the dashboard no longer loads a financial-history panel for these records. No wallet, payment, account, or Experiment 10 operation reads this dataset.

Run Lab 11.1 from the project root after the platform backend and isolated Chapter 11 cluster are available:

```powershell
$env:CHAPTER11_MONGO_URI = 'mongodb://localhost:37317'
..\.venv\Scripts\python.exe .\lab-11\lab11_1_shard_key.py
```

The evaluator first verifies that exactly 1,400 uniquely identified records with the expected source markers exist. If that check fails, it writes no Chapter 11 collections. It then reads the source and writes only the four `chapter11.transactions_by_*` collections used for this evaluation. It checks that its Chapter 11 connection is `mongos` and refuses to alter an existing collection with a different shard key.

The generated `reports/lab11_1_decision_matrix.json` and `.md` contain measured cardinalities, source value frequencies, per-shard counts and storage, write elapsed time, and explain-plan routing. The recommendation is calculated from the measured plans, shard balance, key progression, and workload weights. It can change as the cluster placement or measured inputs change. The reports are also read by `/api/chapter11/reports/decision-matrix` for the Shard Key Evaluation tab.

Lab 11.2 remains independent. It uses `orders_by_timestamp` and `orders_by_hashed_user`, each with its own 10,000-row benchmark fixture. It does not read `demo_financial_activity` or the four 11.1 transaction collections.

# Shard Key Evaluation

- Captured UTC: `2026-10-30T09:11:01.035265+00:00`
- Dataset: `Transaction Dataset` (1,400 persisted records from `chapter07.demo_financial_activity`)
- MongoDB `7.0.45` through mongos; shards: c11-shard1-rs, c11-shard2-rs
- Recommendation: **customer_id** — customer_id scored highest (0.615) under the displayed workload weights using measured query plans, shard balance, and key progression. Hashed keys can improve distribution but reduce range locality; here their measured results are shown rather than assumed.
- Scoring: 70% measured weighted single-shard query coverage + 20% measured shard balance + 10% non-monotonic insert-key credit.
- Every cardinality, distribution, write, and query value below was measured from the source collection or running cluster.

| Candidate | Shard key | Cardinality | Shard distribution | Query routing | Range efficiency | Hotspot assessment | Fit |
|---|---|---|---|---|---|---|---|
| order_id | {"order_id": 1} | 1,400/1,400 distinct; 1–1 rows/value | c11-shard1-rs: 700, c11-shard2-rs: 700 | customer id equality: 2 shard(s); order id equality: 1 shard(s); date range: 2 shard(s); customer id range: 2 shard(s) | Date range: scatter/gather; customer range: scatter/gather | High: input key values increase in insert order; the active upper range is a write hotspot risk. | Measured score 0.410 |
| customer_id | {"customer_id": 1} | 100/1,400 distinct; 14–14 rows/value | c11-shard1-rs: 700, c11-shard2-rs: 700 | customer id equality: 1 shard(s); order id equality: 2 shard(s); date range: 2 shard(s); customer id range: 2 shard(s) | Date range: scatter/gather; customer range: scatter/gather | Low in this dataset: values are not monotonic and the most frequent key represents 1.0% of rows. | Measured score 0.615 |
| order_date | {"order_date": 1} | 1,400/1,400 distinct; 1–1 rows/value | c11-shard1-rs: 699, c11-shard2-rs: 701 | customer id equality: 2 shard(s); order id equality: 2 shard(s); date range: 2 shard(s); customer id range: 2 shard(s) | Date range: scatter/gather; customer range: scatter/gather | High: input key values increase in insert order; the active upper range is a write hotspot risk. | Measured score 0.200 |
| hashed customer_id | {"customer_id": "hashed"} | 100/1,400 distinct; 14–14 rows/value | c11-shard1-rs: 630, c11-shard2-rs: 770 | customer id equality: 1 shard(s); order id equality: 2 shard(s); date range: 2 shard(s); customer id range: 2 shard(s) | Date range: scatter/gather; customer range: scatter/gather | Low in this dataset: values are not monotonic and the most frequent key represents 1.0% of rows. | Measured score 0.595 |

## Query plan details

### order_id

Actual frequency range: 1–1 records per distinct key; most frequent key share 0.07%.
Actual per-shard storage: [('c11-shard1-rs', 700, 180224), ('c11-shard2-rs', 700, 118784)]

| Query | Shards targeted | Scatter/gather | Returned | Examined | Time ms |
|---|---|---|---:|---:|---:|
| customer_id_equality | c11-shard1-rs, c11-shard2-rs | yes | 14 | 1400 | 1 |
| order_id_equality | c11-shard2-rs | no | 1 | 1 | 0 |
| date_range | c11-shard1-rs, c11-shard2-rs | yes | 701 | 1400 | 1 |
| customer_id_range | c11-shard1-rs, c11-shard2-rs | yes | 714 | 1400 | 1 |

### customer_id

Actual frequency range: 14–14 records per distinct key; most frequent key share 1.00%.
Actual per-shard storage: [('c11-shard1-rs', 700, 249856), ('c11-shard2-rs', 700, 118784)]

| Query | Shards targeted | Scatter/gather | Returned | Examined | Time ms |
|---|---|---|---:|---:|---:|
| customer_id_equality | c11-shard1-rs | no | 14 | 14 | 0 |
| order_id_equality | c11-shard1-rs, c11-shard2-rs | yes | 1 | 1400 | 1 |
| date_range | c11-shard1-rs, c11-shard2-rs | yes | 701 | 1400 | 1 |
| customer_id_range | c11-shard1-rs, c11-shard2-rs | yes | 714 | 714 | 2 |

### order_date

Actual frequency range: 1–1 records per distinct key; most frequent key share 0.07%.
Actual per-shard storage: [('c11-shard1-rs', 699, 180224), ('c11-shard2-rs', 701, 118784)]

| Query | Shards targeted | Scatter/gather | Returned | Examined | Time ms |
|---|---|---|---:|---:|---:|
| customer_id_equality | c11-shard1-rs, c11-shard2-rs | yes | 14 | 1400 | 1 |
| order_id_equality | c11-shard1-rs, c11-shard2-rs | yes | 1 | 1400 | 1 |
| date_range | c11-shard1-rs, c11-shard2-rs | yes | 701 | 701 | 1 |
| customer_id_range | c11-shard1-rs, c11-shard2-rs | yes | 714 | 1400 | 2 |

### hashed customer_id

Actual frequency range: 14–14 records per distinct key; most frequent key share 1.00%.
Actual per-shard storage: [('c11-shard1-rs', 630, 110592), ('c11-shard2-rs', 770, 126976)]

| Query | Shards targeted | Scatter/gather | Returned | Examined | Time ms |
|---|---|---|---:|---:|---:|
| customer_id_equality | c11-shard1-rs | no | 14 | 14 | 1 |
| order_id_equality | c11-shard1-rs, c11-shard2-rs | yes | 1 | 1400 | 1 |
| date_range | c11-shard1-rs, c11-shard2-rs | yes | 701 | 1400 | 1 |
| customer_id_range | c11-shard1-rs, c11-shard2-rs | yes | 714 | 1400 | 2 |

## Trade-offs

A hashed key generally spreads different key values across hash ranges, while removing the ordering needed for efficient customer or date range scans. The measured cardinality and shard counts above show how this particular dataset landed; they are not guarantees for other workloads.

The four `transactions_by_*` collections are dedicated only to this evaluation. Experiment 11.2 continues to use its independent 10,000-row timestamp and hashed-user collections.

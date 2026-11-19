# Experiment 11 — Viva Guide

## Why shard keys matter

A shard key determines how MongoDB divides a collection and routes reads and writes. A useful key must fit the real query patterns, have enough distinct values, avoid harmful skew, and support an acceptable balance between locality and distribution. A shard key is a workload decision, not a universally optimal field.

## Four Lab 11.1 candidates

- **`order_id` (range):** exact order lookups and ordered order-ID ranges can target shards. Sequential IDs can funnel new writes to the newest range.
- **`customer_id` (range):** preserves customer-ID locality and can target customer lookups. Uneven customer activity or ordered customer IDs can create skew; very active customers can concentrate writes.
- **`order_date` (range):** makes date windows efficient. Increasing dates send current writes to the active newest range, creating a possible write hotspot.
- **Hashed `customer_id`:** spreads distinct customer values through hash ranges and still targets exact customer equality. It loses customer/date range ordering, and one unusually active customer remains one key value.

The generated decision matrix recommends hashed customer ID for its stated customer-centric, write-active example. A date-range-heavy or order-ID-range-heavy service could choose a different key.

## Hotspots, targeting, and scatter/gather

A monotonically increasing range key keeps the newest inserts at one active chunk edge. Existing documents can be distributed over multiple shards while the live write edge remains concentrated. Hashing spreads distinct key values across ranges, reducing that monotonic edge; it cannot split a single repeated hot key by itself.

When a query includes a shard key, `mongos` can target the shard ranges that own matching values. Without a useful shard-key predicate, it sends the query to multiple shards and combines their results: scatter/gather. Scatter/gather works, but can use more network and shard resources and increase tail latency.

## What this experiment measured

Lab 11.2 inserts the same 10,000 reproducible benchmark records into two isolated collections: one range-sharded by timestamp and one hashed by user ID. It reads owned-document distribution from `$shardedDataDistribution`, per-shard counts and sizes from `$collStats`, and execution statistics and targets from `explain()` through mongos.

The current captured snapshot on MongoDB 7.0.45 has 10,000 rows in each collection. Timestamp-owned counts measured 5,000 on each shard; hashed-user counts measured 5,014 and 4,986. The timestamp collection's exact user lookup reached both shards and examined 10,000 documents; its recent 100-record date range reached one shard and examined 100. The hashed user equality lookup reached one shard and examined one record; its recent date range scattered to both shards and examined 10,000. These are actual values from one run, not expected ratios or performance guarantees. Storage values, write duration, execution times, and raw plans are in [lab11_2_measurements.md](reports/lab11_2_measurements.md) and its adjacent JSON file.

## Limitations

- The cluster is a local educational setup with two single-member shards, not a production replica-set deployment.
- The fixture has 10,000 distinct users and timestamps; real customer/indicator skew and duplicate hot keys may behave differently.
- The measurements capture a small run and two query shapes. They do not model concurrent application traffic, durability requirements, or long-term balancing.
- Millisecond execution times are environment-dependent and should not be treated as a benchmark guarantee.
- A production design needs representative load tests, key cardinality/skew analysis, capacity planning, and an evaluation of compound keys for combined entity-equality and time-range queries.

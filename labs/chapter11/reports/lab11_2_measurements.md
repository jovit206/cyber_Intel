# Lab 11.2 — Shard Key Hotspot Detection

- Captured at (UTC): `2026-10-30T09:06:44.797251+00:00`
- MongoDB version: `7.0.45`
- Router: `mongos` at the configured Chapter 11 endpoint
- Shards: c11-shard1-rs, c11-shard2-rs
- Requested insert volume: 10,000 documents per collection
- Distribution is measured from MongoDB; no target ratio is assumed.

## `orders_by_timestamp`

Shard key: `{'event_at': 1}`
Stored documents: **10,000** · distinct users: **10,000** · distinct timestamps: **10,000**
Insert/upsert elapsed time: **0.919 s**

### Actual per-shard counts and storage

| Shard | Owned documents | $collStats count | Storage bytes | Logical bytes | Index bytes |
|---|---:|---:|---:|---:|---:|
| c11-shard1-rs | 5000 | 5000 | 208896 | 705000 | 303104 |
| c11-shard2-rs | 5000 | 5000 | 208896 | 705000 | 303104 |

### Query plans

| Query | Targeted shards | Scatter/gather | Returned | Examined | Time ms |
|---|---|---|---:|---:|---:|
| exact user lookup (no timestamp shard key) | c11-shard1-rs, c11-shard2-rs | yes | 1 | 10000 | 6 |
| recent time range (timestamp shard key) | c11-shard2-rs | no | 100 | 100 | 2 |

## `orders_by_hashed_user`

Shard key: `{'user_id': 'hashed'}`
Stored documents: **10,000** · distinct users: **10,000** · distinct timestamps: **10,000**
Insert/upsert elapsed time: **0.423 s**

### Actual per-shard counts and storage

| Shard | Owned documents | $collStats count | Storage bytes | Logical bytes | Index bytes |
|---|---:|---:|---:|---:|---:|
| c11-shard2-rs | 4986 | 4986 | 208896 | 703026 | 360448 |
| c11-shard1-rs | 5014 | 5014 | 212992 | 706974 | 376832 |

### Query plans

| Query | Targeted shards | Scatter/gather | Returned | Examined | Time ms |
|---|---|---|---:|---:|---:|
| exact user lookup (hashed shard key) | c11-shard1-rs | no | 1 | 1 | 1 |
| recent time range (not a shard key) | c11-shard1-rs, c11-shard2-rs | yes | 100 | 10000 | 3 |

## Interpretation

An exact equality query on the hashed user shard key is targetable to one shard. A user query against the timestamp-sharded collection does not include its shard key and is broadcast. The timestamp range query is routed by its time range; the hashed collection cannot use its hash key to target that date range and scatters it.

Increasing timestamps put the active insert edge on one range/chunk at a time. Hashed user values avoid a monotonic insert edge when there are many distinct users, though a single hot user remains one shard-key value. The raw explain plans and per-shard distribution records are in the adjacent JSON report.

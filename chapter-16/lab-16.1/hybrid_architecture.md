# Hybrid University Data Architecture

## Responsibilities and source of truth

- **MongoDB (`university_db`) — operational profiles, courses, and enrollment state.** The
  application-facing document model keeps student `course_ids` and course `student_ids` arrays
  for convenient bounded views. A write service updates both views in one MongoDB transaction
  when the deployment is a replica set, or uses an idempotent outbox/reconciliation process on a
  standalone server.
- **Cassandra (`university_data`) — append-oriented audit/event history.** Partition event tables
  by the entity whose history is queried and cluster by time. Audit records are immutable, have a
  stable event ID, event timestamp, actor, action, and payload. The Lab 16.1 enrollment tables
  are query-optimized projections; audit event tables are a separate workload.
- **Neo4j — prerequisite graph and learning paths.** Store `(:Course)-[:REQUIRES]->(:Course)` and
  `(:Student)-[:COMPLETED]->(:Course)`; traverse prerequisite chains and recommend next courses.
  Operational enrollment remains sourced from MongoDB.

## Example data models and queries

### MongoDB

```javascript
{ student_id: "S001", name: "Ava Patel", course_ids: ["C101", "C202"] }
{ course_id: "C101", course_name: "Introduction to Python", student_ids: ["S001", "S002"] }
db.students.find({student_id: "S001"})
db.courses.find({student_ids: "S001"})
```

### Cassandra

```sql
CREATE TABLE enrollment_events_by_student (
  student_id text, event_time timestamp, event_id uuid, action text, course_id text,
  PRIMARY KEY ((student_id), event_time, event_id)
) WITH CLUSTERING ORDER BY (event_time DESC, event_id ASC);
SELECT * FROM enrollment_events_by_student
WHERE student_id = 'S001' LIMIT 20;
```

Partition by the queried student or course. Avoid unrestricted scans and model separate tables
for materially different access patterns.

### Neo4j

```cypher
MATCH (c:Course {course_id: $course_id})-[:REQUIRES*1..5]->(prerequisite:Course)
RETURN DISTINCT prerequisite.course_id, prerequisite.course_name
ORDER BY prerequisite.course_id;
```

The upper path bound is deliberate: prerequisite cycles or long graphs must not create
unbounded traversals.

## Synchronization and consistency

MongoDB owns operational changes. A transactional outbox records each accepted enrollment or
course edit in the same MongoDB transaction as its change. Independent idempotent consumers
publish events to Cassandra and update Neo4j projections using stable event IDs and monotonic
versions. Track consumer offsets and dead-letter failures; retry with backoff and periodically
reconcile projection counts/hashes against MongoDB. Define acceptable projection lag and provide
an operational way to replay events.

Do not attempt an uncoordinated distributed transaction across the three engines. A failure
between writes is handled by the outbox, retries, and reconciliation. Enrollment removals require
explicit tombstone events so stale secondary projections do not resurrect deleted edges.

## Trade-offs

Polyglot persistence aligns storage with workload: documents fit changing operational records,
Cassandra fits high-volume partitioned append histories, and a graph store fits bounded
relationship traversal. The costs are multiple backups, access-control systems, schema and
deployment tooling, monitoring, data duplication, eventual consistency, and operator expertise.
Keep each projection purposeful; do not copy all fields into every engine by default.

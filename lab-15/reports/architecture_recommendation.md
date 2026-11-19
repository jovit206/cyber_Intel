# TaskTracker Tenant Architecture Recommendation

## Strategy comparison

| Factor | Document-level isolation | Collection-per-tenant |
|---|---|---|
| Tenant isolation | Every query and write must include a trusted `tenant_id`; enforce this in shared repository helpers and authorization tests. | Collection selection separates ordinary reads, but authorization must still verify membership before selecting a collection. |
| Query performance | Compound indexes begin with `tenant_id`; one shared collection is operationally simple, though large tenant skew needs measurement and possibly sharding. | Smaller per-tenant collections can support tenant-specific index or workload tuning. Thousands of collections add catalog/index overhead. |
| Cross-tenant analytics | Direct aggregation over one collection with `$match` and `$group`. | Requires `$unionWith`, application fan-out, or a separate analytics store; operational cost increases with tenant count. |
| Backup and restore | Database/collection backups are simple, while extracting one tenant requires a namespace/query filter or application export. | A tenant collection can be dumped or restored independently, with careful namespace mapping and access controls. |
| Schema evolution | One shared schema and migration path encourage consistent rollout. | Different collections can drift; coordinated migrations must iterate across every tenant collection. |
| Scalability | Works well for many small/medium tenants; isolate unusually large tenants with a hybrid or sharding strategy when measured need justifies it. | Suitable for a bounded number of tenants with isolation needs; collection and index counts grow with tenant count. |
| Maintenance | Fewer collections, indexes, backups, and monitoring targets; strict query discipline is essential. | Per-tenant controls are explicit but provisioning, migrations, monitoring, and cleanup are more complex. |

## Recommendation

Start most SaaS products with document-level tenant isolation. It supports many tenants, shared
cross-tenant analytics, uniform schema changes, and centralized operations. Put `tenant_id` in
every tenant-owned document and make it mandatory in every database selector. Derive it from a
validated identity token, never from a task request or URL parameter. Keep compound tenant-first
indexes and enforce isolation with automated adversarial tests.

Move selected tenants to collection-per-tenant isolation, or adopt a hybrid strategy, when business,
performance, backup/restore, contractual, or regulatory requirements justify the extra operations.
Migration should be measured and reversible; this lab copies records while retaining the original
shared collection as the source of truth.

## Lab boundary

This demonstration uses one `tasktracker` database and five tenant collections. It does not claim
that a separate collection is a security boundary against a database administrator, nor does it
replace MongoDB authorization, encryption, or application-level tenant checks.

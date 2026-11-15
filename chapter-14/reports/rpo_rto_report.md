# Chapter 14 RPO and RTO

Measurements below are from the live Chapter 14 restore benchmark run on 2026-11-11. They are
machine- and dataset-specific, not service-level guarantees.

## Recovery point objective (RPO)

- **Planned target:** hourly successful backups imply a maximum planned data-loss window of
  approximately 1 hour.
- **Measured outage/data-loss window:** not measured. No production outage or backup-loss event
  was simulated; the benchmark validates recovery of generated test data only.

## Recovery time objective (RTO)

| Dataset | Measured backup | Measured restore |
|---:|---:|---:|
| 1,000 documents | 1.343151 s | 2.838609 s |
| 10,000 documents | 3.450714 s | 10.308943 s |
| 100,000 documents | 7.904497 s | 77.355650 s |

- **Measured restore component:** the largest observed `mongorestore` duration was 77.355650
  seconds for 100,000 documents. The benchmark CSV contains the complete measurements and dump
  sizes.
- **Application restart time:** not measured.
- **Scope:** restore timing covers the `mongorestore` command only; it excludes application
  restart, service-level checks, failover, network recovery, and external dependencies.
- **Result:** this run's measured database-restore component is approximately 1 minute 17.36
  seconds at 100,000 documents. This is an observation, not an end-to-end RTO commitment.

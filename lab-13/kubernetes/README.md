# Kubernetes deployment notes

The user-facing deployment, verification, pod-recovery, and cleanup instructions are in
[`../README.md`](../README.md). Apply the manifests only after creating the `mongo-ha` namespace
and the `mongo-keyfile` and `mongo-credentials` Secrets. The supported end-to-end path is:

```bash
bash ../scripts/deploy-kubernetes.sh
bash ../scripts/verify-kubernetes.sh
bash ../scripts/test-pod-resiliency.sh
```

The deploy script generates/uses the local keyfile, reads credentials from the ignored
`../.env`, creates Secrets without printing their values, applies the headless Service and
StatefulSet, and runs the replica-set initialization Job. `mongo-secret.yaml.example` is a
placeholder template and must not be applied as-is.

The headless Service and StatefulSet share the `app.kubernetes.io/name: mongodb` selector. The
StatefulSet `serviceName` must remain `mongo-headless-svc`; changing or omitting it breaks the
stable member DNS names used by `rs.initiate()`. Each ordinal gets a separate 10 GiB PVC. Select a
StorageClass explicitly in `mongo-statefulset.yaml` if the Kubernetes cluster has no default.

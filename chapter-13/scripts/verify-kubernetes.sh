#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="mongo-ha"
kubectl get pods,statefulset,pvc --namespace "$NAMESPACE" -o wide
kubectl exec -i --namespace "$NAMESPACE" mongo-0 -- /bin/sh -ec \
  'mongosh --quiet --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --file /dev/stdin' \
  < "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/scripts/verify-kubernetes-replica-set.js"

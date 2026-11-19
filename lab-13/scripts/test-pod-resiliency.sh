#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="mongo-ha"
POD_PREFIX="mongo"
MARKER="$(date -u +%Y%m%dT%H%M%SZ)-$$"
MONGO_SHELL='mongosh --quiet --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin'

kubectl exec --namespace "$NAMESPACE" mongo-0 -- /bin/sh -ec \
  "mongosh --quiet --uri \"mongodb://\$MONGO_INITDB_ROOT_USERNAME:\$MONGO_INITDB_ROOT_PASSWORD@mongo-0.mongo-headless-svc:27017,mongo-1.mongo-headless-svc:27017,mongo-2.mongo-headless-svc:27017/admin?replicaSet=rs0&authSource=admin\" --eval 'db.getSiblingDB(\"lab13\").pod_resiliency.insertOne({_id: \"$MARKER\", createdAt: new Date()})'"

target=""
for ordinal in 0 1 2; do
  candidate="${POD_PREFIX}-${ordinal}"
  state="$(kubectl exec --namespace "$NAMESPACE" "$candidate" -- /bin/sh -ec \
    "$MONGO_SHELL --eval 'print(db.adminCommand({replSetGetStatus: 1}).myState)'")"
  if [[ "$state" == "2" ]]; then
    target="$candidate"
    break
  fi
done

if [[ -z "$target" ]]; then
  echo "No SECONDARY member was found; refusing to delete a PRIMARY." >&2
  exit 1
fi
claim="$(kubectl get pod "$target" --namespace "$NAMESPACE" \
  -o jsonpath='{.spec.volumes[?(@.name=="mongo-data")].persistentVolumeClaim.claimName}')"
if [[ -z "$claim" ]]; then
  echo "Could not identify $target's MongoDB data PVC; refusing to delete the pod." >&2
  exit 1
fi

echo "The test will delete SECONDARY pod $target and verify its PVC-backed data returns."
echo "Data claim to preserve: $claim"
read -r -p "Type DELETE-$target to continue: " confirmation
if [[ "$confirmation" != "DELETE-$target" ]]; then
  echo "Cancelled; no pod was deleted."
  exit 1
fi

kubectl delete pod "$target" --namespace "$NAMESPACE"
kubectl wait --for=condition=Ready "pod/$target" --namespace "$NAMESPACE" --timeout=10m

deadline=$((SECONDS + 300))
while (( SECONDS < deadline )); do
  if kubectl exec --namespace "$NAMESPACE" "$target" -- /bin/sh -ec \
    "$MONGO_SHELL --eval 'if (db.adminCommand({replSetGetStatus: 1}).myState !== 2) quit(1); db.getMongo().setReadPref(\"secondaryPreferred\"); const found = db.getSiblingDB(\"lab13\").pod_resiliency.findOne({_id: \"$MARKER\"}); if (!found) quit(1); printjson({_id: found._id})'" \
    >/dev/null 2>&1 &&
    [[ "$(kubectl get pod "$target" --namespace "$NAMESPACE" \
      -o jsonpath='{.spec.volumes[?(@.name=="mongo-data")].persistentVolumeClaim.claimName}')" == "$claim" ]]; then
    echo "$target returned Ready and read its replicated marker $MARKER after recreation."
    "$(dirname -- "${BASH_SOURCE[0]}")/verify-kubernetes.sh"
    exit 0
  fi
  sleep 5
done

echo "The recreated pod did not rejoin with the marker within 300 seconds." >&2
kubectl describe pod "$target" --namespace "$NAMESPACE"
kubectl logs "$target" --namespace "$NAMESPACE" --tail=100
exit 1

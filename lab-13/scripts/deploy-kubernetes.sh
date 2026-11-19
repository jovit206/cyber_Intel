#!/usr/bin/env bash
set -euo pipefail

CHAPTER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
KUBE_DIR="$CHAPTER_DIR/kubernetes"
NAMESPACE="mongo-ha"
cd "$CHAPTER_DIR"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required." >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "Missing lab-13/.env. Copy .env.example to .env and set independent random hex passwords." >&2
  exit 1
fi

source "$CHAPTER_DIR/scripts/load-env.sh"
normalize_chapter_env "$CHAPTER_DIR/.env"
set -a
source "$CHAPTER_DIR/.env"
set +a

for name in MONGO_INITDB_ROOT_USERNAME MONGO_INITDB_ROOT_PASSWORD; do
  if [[ -z "${!name:-}" || "${!name}" == REPLACE_* ]]; then
    echo "$name must be set to a non-placeholder value in lab-13/.env." >&2
    exit 1
  fi
done

if [[ ! -s "$CHAPTER_DIR/secrets/mongodb-keyfile" ]]; then
  "$CHAPTER_DIR/scripts/generate-keyfile.sh"
fi
kubectl config current-context >/dev/null
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_DIR"' EXIT
umask 077
printf '%s' "$MONGO_INITDB_ROOT_USERNAME" > "$TEMP_DIR/username"
printf '%s' "$MONGO_INITDB_ROOT_PASSWORD" > "$TEMP_DIR/password"

kubectl create secret generic mongo-keyfile \
  --namespace "$NAMESPACE" \
  --from-file=keyfile="$CHAPTER_DIR/secrets/mongodb-keyfile" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic mongo-credentials \
  --namespace "$NAMESPACE" \
  --from-file=username="$TEMP_DIR/username" \
  --from-file=password="$TEMP_DIR/password" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f "$KUBE_DIR/mongo-headless-svc.yaml"
kubectl apply -f "$KUBE_DIR/mongo-statefulset.yaml"
kubectl rollout status statefulset/mongo --namespace "$NAMESPACE" --timeout=10m

kubectl delete job mongo-init --namespace "$NAMESPACE" --ignore-not-found
kubectl apply -f "$KUBE_DIR/mongo-init-job.yaml"
kubectl wait --for=condition=complete job/mongo-init --namespace "$NAMESPACE" --timeout=10m

"$CHAPTER_DIR/scripts/verify-kubernetes.sh"

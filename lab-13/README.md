# Chapter 13: MongoDB High Availability and Kubernetes Deployment

This chapter builds the same authenticated, three-member MongoDB 7.0 replica set in two
independent environments: Docker Compose (Lab 13.1) and Kubernetes (Lab 13.2). It also includes
an optional MongoDB Atlas M0 Terraform configuration. Each local deployment uses separate names,
ports, storage, and credentials; neither changes earlier repository labs.

## Objectives

- Secure replica-set member traffic with a shared MongoDB keyfile and authenticate administrators
  with a locally supplied username and password.
- Persist each member's data independently and verify one PRIMARY plus two SECONDARY members.
- Exercise majority writes, reads, primary discovery, automatic driver reconnection, and primary
  election after a deliberate `rs.stepDown()`.
- Run a least-privilege Prometheus MongoDB exporter.
- Use a Kubernetes headless Service and StatefulSet for stable member DNS and per-pod persistent
  volume claims; test pod recreation and replicated-data recovery.
- Keep Atlas provisioning separate from local deployments and avoid committing secrets or state.

## Prerequisites

### Lab 13.1

- Docker Engine/Desktop with Docker Compose v2 (`docker compose`), 4 CPU threads and about 4 GB of
  available memory for three MongoDB nodes and the exporter.
- OpenSSL for key generation.
- Bash 4+ (Linux, WSL, or Git Bash on Windows). Run the shell scripts with `bash` if the
  executable bit is not set.
- The Compose ports `27117`, `27118`, `27119`, and `19216` available on loopback.

### Lab 13.2

- `kubectl` configured for a Kubernetes cluster. Local options include kind and minikube.
- Three schedulable pods with approximately 1.5 CPU and 3 GiB of memory in total, plus at least
  three 10 GiB persistent-volume claims.
- A default StorageClass capable of provisioning `ReadWriteOnce` volumes, or a cluster-specific
  StorageClass configured in the StatefulSet manifest.
- The same Bash, OpenSSL, and `.env` setup used by Lab 13.1.

The StatefulSet is not a substitute for a production MongoDB Kubernetes operator. A local
single-node Kubernetes cluster can schedule all three pods, but is still one failure domain; its
storage and node outages do not provide infrastructure-level high availability.

These labs enable MongoDB authentication and replica-set keyfile authentication, but do not
configure TLS certificates, encrypted Kubernetes Secret storage, a backup policy, or restrictive
NetworkPolicies. Do not expose them to untrusted networks; add those controls and a managed secret
store before using a production cluster.

### Terraform / Atlas

- Terraform 1.5 or later.
- A MongoDB Atlas organization ID and Atlas API public/private keys with permission to create
  projects and clusters.
- An IPv4 CIDR that identifies only the machine/network that must connect to the cluster.

## Folder structure

```text
chapter-13/
├── .env.example
├── .dockerignore
├── .gitattributes
├── .gitignore
├── Dockerfile.failover
├── docker-compose.yml
├── scripts/
│   ├── deploy-kubernetes.sh
│   ├── create-exporter-user.js
│   ├── generate-keyfile.sh
│   ├── load-env.sh
│   ├── init-replica-set.js
│   ├── start-cluster.sh
│   ├── stop-cluster.sh
│   ├── test-failover.py
│   ├── test-pod-resiliency.sh
│   ├── verify-exporter.py
│   ├── verify-exporter.sh
│   ├── verify-kubernetes-replica-set.js
│   ├── verify-kubernetes.sh
│   ├── verify-replica-set.js
│   └── verify-replica-set.sh
├── kubernetes/
│   ├── README.md
│   ├── mongo-headless-svc.yaml
│   ├── mongo-init-job.yaml
│   ├── mongo-secret.yaml.example
│   └── mongo-statefulset.yaml
└── terraform/
    ├── main.tf
    ├── outputs.tf
    ├── terraform.tfvars.example
    ├── variables.tf
    └── versions.tf
```

Generated keyfiles and `.env` values are local-only. Terraform state and provider lock files are
also ignored in this chapter's `.gitignore`.

## Shared local secret setup

From a Bash terminal:

```bash
cd chapter-13
cp .env.example .env
openssl rand -hex 32
openssl rand -hex 32
```

Put the first random value in `MONGO_INITDB_ROOT_PASSWORD` and the independent second value in
`MONGO_EXPORTER_PASSWORD`. Keep the username values or change them locally. Use hexadecimal
passwords (or otherwise URL-encode credentials if you change the exporter URI construction) and
never commit `.env`. The exporter password is for a separate `clusterMonitor` account; it is not
the administrative password.

Generate the shared keyfile:

```bash
bash scripts/generate-keyfile.sh
```

The script generates 756 random bytes encoded as base64, writes the ignored
`secrets/mongodb-keyfile` file with mode `0400`, and refuses to replace an existing key. Replacing
a key on a running cluster can partition it; key rotation requires a planned rolling procedure.
The deployment scripts normalize Windows CRLF line endings in the local `.env`, enforce file mode
`0600`, and then read it or pass it to Compose.
The Compose entrypoint copies the mounted file into the container, sets owner `mongodb`, and
enforces mode `0400` before starting `mongod`.

## Lab 13.1 — Docker Compose replica set

### Design

`docker-compose.yml` defines three MongoDB 7.0.45 services (`mongo1`, `mongo2`, `mongo3`) on the
project-scoped `chapter13-mongo-net` bridge network. All start with `--replSet rs0 --auth --keyFile`;
each has a separate named volume, an authenticated ping health check, a one-minute graceful stop
period, and CPU/memory limits. Host ports bind only to `127.0.0.1` and intentionally differ from
earlier labs.

MongoDB's internal replica-set addresses are `mongo1:27017`, `mongo2:27017`, and `mongo3:27017`.
The generated keyfile authenticates replica-set members; it is separate from application
user/password authentication. `start-cluster.sh` creates a least-privilege `clusterMonitor` user,
initiates (or validates) `rs0`, waits for all three healthy members, and starts the Percona exporter.
The failover test runs inside the Compose network so the driver's discovered MongoDB hostnames
remain resolvable.

### Optional Prometheus and Grafana

The exporter remains part of the normal replica-set startup. Prometheus and Grafana are opt-in so
an existing Chapter 13 deployment does not gain new public listeners or require new credentials
when it is started. Set unique `GRAFANA_ADMIN_USER` and `GRAFANA_ADMIN_PASSWORD` values in the
ignored `chapter-13/.env`, then start only the observability profile:

```bash
docker compose --env-file .env --profile observability up -d prometheus grafana
```

Prometheus scrapes the existing exporter over the private Compose network. Its UI is bound to
`127.0.0.1:19090`; Grafana is bound to `127.0.0.1:13000` (change the `*_BIND_PORT` variables if
those loopback ports are in use). Grafana provisions a Prometheus data source and a Chapter 13
dashboard. Dashboard panels query exporter metrics; an empty panel means that metric was not
returned by the configured exporter and is not replaced with sample values.

The Prometheus and Grafana data use separate named volumes. `docker compose down` retains all five
MongoDB/observability volumes; do not use `down -v` unless you intentionally want to erase them.
The app's Chapter 13 workspace reports Prometheus/Grafana links only when `LAB13_PROMETHEUS_URL`,
`LAB13_PROMETHEUS_PUBLIC_URL`, `LAB13_GRAFANA_URL`, and `LAB13_GRAFANA_PUBLIC_URL` are configured
for the backend runtime. The backend URL must be reachable from the app process; the public URL
must be reachable from the browser.

### Application operations API

The signed-in administrator workspace uses the existing application session and admin role check.
The new routes are:

- `GET /api/ops/overview` — latest sampled application replica-set data and configured integration
  readiness.
- `GET /api/ops/metrics/deployment?minutes=30` — stored `serverStatus`/replica-set samples and
  observed election markers for 1–30 minutes.
- `POST /api/ops/lab13/failover` — requires `{"confirm":true}` and a 5–120 second duration; it
  records audit intent, requests a non-forced stepdown, and returns success only after observing a
  different primary.
- `POST /api/services/lab13/kubernetes` — requires `{"confirm":true}` and runs only the checked-in
  `scripts/deploy-kubernetes.sh` after verifying local prerequisites. It never accepts a command
  from the request.

The operations API samples the MongoDB replica set configured for the application. The Chapter 13
Compose replica set and Kubernetes cluster are separate deployments; their status is not inferred
from the app cluster. Kubernetes is reported as unavailable unless the backend environment has a
working `kubectl` context and the ignored Chapter 13 credentials/keyfile. Atlas status reports
configuration presence only. The application does not run Terraform apply/destroy.

### Configure and start

After completing the shared secret setup:

```bash
bash scripts/start-cluster.sh
```

The startup script starts only this chapter's MongoDB nodes, authenticates to `mongo1`, runs the
idempotent initialization script, and waits up to three minutes for exactly one PRIMARY and two
SECONDARY members. It never removes volumes or reconfigures a pre-existing, mismatched replica set.

Run verification again at any time:

```bash
bash scripts/verify-replica-set.sh
docker compose --env-file .env ps
```

Expected replica-set member names are `mongo1:27017`, `mongo2:27017`, and `mongo3:27017`, with
`health: 1` for each and exactly one PRIMARY. To inspect the replica-set status directly:

```bash
docker compose --env-file .env exec mongo1 mongosh --quiet \
  --username "$MONGO_INITDB_ROOT_USERNAME" \
  --password "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin --eval 'rs.status()'
```

If running that command in a fresh shell, export the two admin variables from `.env` first or
enter their local values. Do not put credentials in source files or shared terminal transcripts.

### PyMongo reads, writes, and primary failover

The included Python client uses PyMongo 4.15.3 in an isolated test image. It continuously inserts
documents using majority write concern, reads each inserted document back from the primary, and
prints the current primary reported by the replica set.

```bash
docker compose --env-file .env --profile tools run --build --rm failover-test \
  --duration 120
```

To deliberately step down the current primary and verify that the PyMongo replica-set client
reconnects, a new primary is elected, and read/write operations resume:

```bash
docker compose --env-file .env --profile tools run --build --rm failover-test \
  --stepdown --duration 120
```

Only run `--stepdown` on this dedicated lab cluster: it causes a temporary interruption of
primary-only operations. The script does not use `force`, and it fails if no new primary is
observed during the test interval. The test data is in the `lab13.failover_probes` collection.

### Prometheus exporter

The `mongodb-exporter` sidecar listens on `9216` inside the project Docker network and is mapped
only to loopback port `19216`:

```bash
curl --fail http://127.0.0.1:19216/metrics
bash scripts/verify-exporter.sh
```

Add the following scrape job to an existing Prometheus configuration when Prometheus runs on the
same host:

```yaml
scrape_configs:
  - job_name: mongodb-chapter13
    metrics_path: /metrics
    static_configs:
      - targets: ["127.0.0.1:19216"]
```

For Prometheus in a container, attach it to the `chapter13-mongo-net` Docker network and target
`mongodb-exporter:9216` instead. Do not publish the exporter or database ports to a public
interface. MongoDB's advertised `mongoN` hostnames are Docker-network names, so replica-set-aware
clients should also run on that network rather than connect from the host through the published
member ports.

### Stop and remove data

```bash
bash scripts/stop-cluster.sh
```

This stops and removes the containers/network while preserving all named data volumes. To
permanently erase the three MongoDB volumes, use the explicit confirmation flow:

```bash
bash scripts/stop-cluster.sh --volumes
```

## Lab 13.2 — Kubernetes StatefulSet

### StatefulSet and headless Service

`mongo-headless-svc.yaml` creates `mongo-headless-svc` with `clusterIP: None` and publishes pod
addresses even before readiness. `mongo-statefulset.yaml` creates three stable identities and
independent `mongo-data-mongo-0`, `mongo-data-mongo-1`, and `mongo-data-mongo-2` PVCs using
`volumeClaimTemplates`. MongoDB members use these stable addresses:

```text
mongo-0.mongo-headless-svc:27017
mongo-1.mongo-headless-svc:27017
mongo-2.mongo-headless-svc:27017
```

The full Kubernetes DNS names add `.mongo-ha.svc.cluster.local`. The StatefulSet uses parallel pod
management so all three members can start before replica-set initialization. An init container
copies the Secret keyfile into a memory-backed volume, sets MongoDB's UID/GID and mode `0400`, and
the `mongod` container mounts that copy read-only. Authenticated startup, readiness, and liveness
probes check a MongoDB ping. CPU and memory requests/limits and a two-pod disruption budget are
included.

The `mongo-init` Job authenticates with the root user, calls `rs.initiate()` with all three stable
DNS names, and waits for one PRIMARY and two SECONDARY members. Kubernetes Secrets are created by
the deploy script directly from the ignored local keyfile and `.env`; secret values are not
rendered into committed manifests.

### Deploy and verify

Use the same `.env` and generated keyfile as in Lab 13.1. A cluster with a default dynamic
StorageClass is the simplest local setup:

```bash
bash scripts/deploy-kubernetes.sh
```

The script creates/updates namespace `mongo-ha`, creates or updates the two Secrets, applies the
headless Service and StatefulSet, waits for all MongoDB pods to become ready, then runs the
initialization Job. The checked-in `mongo-secret.yaml.example` is documentation only; do not apply
it because it contains placeholders.

Inspect pod, StatefulSet, PVC, and replica-set state:

```bash
bash scripts/verify-kubernetes.sh
kubectl get service mongo-headless-svc --namespace mongo-ha
kubectl get pods,statefulset,pvc --namespace mongo-ha -o wide
kubectl logs job/mongo-init --namespace mongo-ha
```

For clusters without a default StorageClass, edit `storageClassName` in
`kubernetes/mongo-statefulset.yaml` to the name of an installed class before deploying. PVCs
remain after a StatefulSet is deleted; this preserves data but also means a fresh deployment may
reuse old replica-set state. Do not delete PVCs unless intentionally resetting the lab.

### Pod deletion and recovery test

The test writes a majority-acknowledged marker, finds a SECONDARY pod, and asks for explicit
confirmation before deleting it. Kubernetes must recreate that stable pod, attach its existing
PVC, and replicate the marker back before the script succeeds:

```bash
bash scripts/test-pod-resiliency.sh
```

It refuses to delete a PRIMARY if no SECONDARY is available. For manual observation in another
terminal:

```bash
kubectl get pods,pvc --namespace mongo-ha --watch
```

The test verifies recovered replicated data. For forensic confirmation that the same claim was
reused, compare the pod's volume claim before and after the test:

```bash
kubectl get pod mongo-1 --namespace mongo-ha \
  -o jsonpath='{.spec.volumes[?(@.name=="mongo-data")].persistentVolumeClaim.claimName}{"\n"}'
```

Replace `mongo-1` with the pod reported by the test. Cleanup removes Kubernetes resources but
preserves PVC data unless you explicitly delete those claims:

```bash
kubectl delete job mongo-init --namespace mongo-ha --ignore-not-found
kubectl delete statefulset mongo --namespace mongo-ha
kubectl delete service mongo-headless-svc --namespace mongo-ha
kubectl delete poddisruptionbudget mongo --namespace mongo-ha --ignore-not-found
kubectl delete secret mongo-keyfile mongo-credentials --namespace mongo-ha
# Destructive data removal; review the claims before running:
kubectl delete pvc --namespace mongo-ha --all
kubectl delete namespace mongo-ha
```

## Docker Compose vs Kubernetes

| Concern | Docker Compose (Lab 13.1) | Kubernetes (Lab 13.2) |
|---|---|---|
| Stable member identity | Private Docker DNS service names | StatefulSet ordinal names and headless-Service DNS |
| Persistent data | Three named Docker volumes | Three PVCs from `volumeClaimTemplates` |
| Secret source | Ignored local keyfile and `.env` | Kubernetes Secrets created from those local files |
| Startup / recovery | Compose health checks and restart policy | Probes, StatefulSet controller, PVC reattachment |
| Replica-set bootstrap | `scripts/init-replica-set.js` | One-shot `mongo-init` Job |
| Monitoring | Percona exporter on loopback port 19216 | Add a separately managed exporter and scrape policy if required |
| Failure domain | Usually one Docker host | Depends on cluster node and storage topology; a local cluster is still one failure domain |

These are separate, standalone lab deployments. Do not point both at the same MongoDB data volume
or reuse the same PVCs.

## Optional MongoDB Atlas free-tier deployment (Terraform)

The Terraform option in `terraform/` provisions an Atlas project, an M0 replica-set cluster, a
database-scoped `readWrite` user, and an IP access-list entry. It does not create or modify either
local deployment.

1. Copy `terraform/terraform.tfvars.example` to `terraform/terraform.tfvars` and set your Atlas
   organization ID, a strong database-user password, and your client IPv4 CIDR. The `.tfvars` file
   is ignored.
2. Provide API keys through the provider's environment variables; never place them in `.tfvars` or
   commit them:

   ```bash
   export MONGODB_ATLAS_PUBLIC_KEY='your-public-key'
   export MONGODB_ATLAS_PRIVATE_KEY='your-private-key'
   ```

3. Review and apply:

   ```bash
   cd terraform
   terraform init
   terraform fmt -check
   terraform validate
   terraform plan -out=chapter13.tfplan
   terraform apply chapter13.tfplan
   terraform output atlas_srv_connection_string
   ```

   Terraform state contains sensitive infrastructure data, including the database user's
   password. Keep the local state private; use a secured remote backend for shared/team use.
   The connection string output does not contain credentials. Add the username/password via a
   secret manager or local client configuration.
4. Remove Atlas resources only when ready:

   ```bash
   terraform destroy
   ```

   Review Terraform's destruction plan before approving it. Atlas M0 availability, supported
   regions, and resource limits can change; confirm the selected region is currently eligible.
   The access-list CIDR has no permissive default—do not replace it with `0.0.0.0/0`.

## Troubleshooting

- **Docker says the keyfile is inaccessible or has unsafe permissions:** regenerate it before
  starting a fresh deployment with `bash scripts/generate-keyfile.sh`; inspect
  `docker compose --env-file .env logs mongo1`. The entrypoint copies and fixes permissions
  inside each container. Do not rotate the key on a running cluster.
- **Compose cannot bind a host port:** check ports `27117`–`27119` and `19216`, then stop the
  conflicting process or change only this chapter's loopback port mappings.
- **Health checks fail with authentication errors:** ensure the local root credentials are
  consistent with the existing data volumes. MongoDB's initialization variables only create an
  initial user on an empty data directory; they do not reset existing users.
- **Replica-set members cannot resolve each other:** keep the configured Docker network and the
  exact internal service hostnames; do not initialize members with `localhost` or host-published
  ports.
- **PyMongo reports DNS/server-selection errors:** run the failover client via
  `docker compose --profile tools run ...`, not directly on the host. The advertised member
  addresses resolve inside the Docker network.
- **Kubernetes PVCs remain Pending:** install/select a StorageClass with dynamic provisioning,
  verify the cluster has storage capacity, and inspect
  `kubectl describe pvc --namespace mongo-ha`.
- **Kubernetes probes fail:** inspect `kubectl logs mongo-0 --namespace mongo-ha`, pod events,
  Secret names, the PVC mount, and keyfile ownership/mode. MongoDB must be able to read the
  keyfile as UID/GID 999.
- **Kubernetes Job fails:** inspect `kubectl logs job/mongo-init --namespace mongo-ha` and verify
  all three pods are Ready, the credentials Secret exists, and pod DNS resolves.
- **Atlas provider plan fails:** confirm the organization ID/API key permissions, public/private
  key environment variable names, selected M0 region, and CIDR. Atlas IP access lists are separate
  from local Docker/Kubernetes network rules.

## Validation and environment limits

The scripts are designed to report failures rather than synthesize success. Run the exact lab
commands above to exercise a live cluster. Compose syntax can be checked without starting
containers:

```bash
docker compose --env-file .env config --quiet
```

Kubernetes manifests can be client-validated without applying them:

```bash
kubectl apply --dry-run=client -f kubernetes/mongo-headless-svc.yaml
kubectl apply --dry-run=client -f kubernetes/mongo-statefulset.yaml
kubectl apply --dry-run=client -f kubernetes/mongo-init-job.yaml
```

The actual results of validation attempts are reported with the implementation; live Kubernetes
and Atlas checks require their respective configured environments.

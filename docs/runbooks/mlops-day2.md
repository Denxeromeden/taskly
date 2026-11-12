# MLFlowOps Day 2 runbook

This runbook covers the Day 2 MLflow workload, data Jobs, training, staging promotion and serving checks. The machine-readable workload interface is in [`../workload-contract.yaml`](../workload-contract.yaml); the infrastructure interface remains [`../foundation-contract.yaml`](../foundation-contract.yaml).

## Day 2 scope and safety boundary

The `ML jobs` workflow is an operator-triggered workflow for `seed`, `add-data` and `train` only. A successful training run automatically creates a promotion Job. Promotion may initialise or update `@staging`; it never changes `@production`.

Day 2 deliberately does not:

- create or change the `@production` alias;
- enable the production Argo CD Application;
- create a public ALB or activate the production Gateway;
- deploy Prometheus, Grafana, Loki or Alloy;
- run Terraform.

Do not use this runbook as approval to apply or destroy infrastructure, rotate credentials, change IAM or push images outside the documented workflows.

## Prerequisites

1. The foundation stacks are already deployed. From an operator workstation with the configured AWS and Kubernetes contexts, run:

   ```bash
   scripts/verify-foundation.sh
   ```

   Resolve any failed foundation checks before continuing. This script checks the foundation layer; it does not prove that all Day 2 workload acceptance checks pass.

2. The GitHub repository has the existing CI role ARN stored as the `AWS_ROLE_ARN` Actions secret and the deployment region stored as the `AWS_REGION` Actions variable. The role trust must allow the `main` branch OIDC subject, and the EKS access entry/RBAC must grant only the existing `mlflowops:deployers` permissions.

3. The `mlflowops-root`, `db-bootstrap`, `mlflow` and `ml-jobs` Argo CD Applications are present. The `serving-staging` Application is intentionally kept disabled until `@staging` exists.

4. The RDS ExternalSecrets are Ready. Do not display or copy Secret values while checking them:

   ```bash
   kubectl -n mlflow get externalsecret mlflow-db-credentials
   kubectl -n ml-jobs get externalsecret tasks-db-credentials
   ```

## Build, scan and publish the workload images

The CI workflow is currently configured for manual dispatch. In GitHub Actions, select `CI security`, choose the `main` branch and run the workflow. The workflow runs its validation gates first, then builds and scans the `mlflow`, `ml-jobs` and `serving` images. On `main`, it publishes each image under the full Git commit SHA to its existing ECR repository. It then updates only the tag in `deploy/charts/mlflow/values-image.yaml` and commits that tag change. The registry URI/account ID is resolved at runtime and is not stored in Git-managed values.

Wait for the whole run, including `update-mlflow-image-tag`, to complete successfully before starting a workload Job. The `ML jobs` workflow reads the immutable tag from `deploy/charts/mlflow/values-image.yaml` and verifies that the tag exists in all three ECR repositories. Do not substitute `latest` or an image from an unscanned build.

Current CI policy exceptions and a scan-gate mismatch:

- The Taskly API dependency audit remains blocking. MLflow/jobs/serving dependency findings are report-only; a `pip-audit` vulnerability exit is converted to a warning, while other command failures still fail the step.
- Checkov runs with `--soft-fail`; findings are visible but do not block.
- TFLint warnings do not block; errors do.
- Trivy filesystem checks for repository secrets and non-Terraform critical misconfigurations remain blocking.
- The Trivy image step currently has `exit-code: "0"`, even though its comment says fixable CRITICAL findings block. As written, image vulnerability findings do not block ECR publication. This is a configuration mismatch with the intended image security gate; do not treat a green CI run as proof that the images are free of CRITICAL vulnerabilities. Resolve this policy before marking the image-scan part of AC-D2-23 complete.

The PR and push triggers in `ci-security.yml` are commented out at present. A manual run does not establish that a PR-triggered, no-AWS CI path has been exercised. Also verify that the deployed IAM role trust policy matches the corrected `main`-branch OIDC subject in the current Terraform source; changing the source alone does not update an already deployed role.

## Run a data or training Job

In GitHub Actions, select `ML jobs`, choose the `main` branch and use `Run workflow`. The workflow serializes runs with the `taskly-mlflow-training` concurrency group and creates uniquely named Kubernetes Jobs. It waits for completion, prints logs and diagnoses failed or timed-out Jobs. It does not use `kubectl apply` to mutate an earlier Job.

### Seed the starter dataset

Choose `operation: seed`. The other data-generation inputs are ignored. The seed operation checks the `tasks` table for rows with a non-null priority. If labeled rows already exist, it succeeds and reports that seeding was skipped; it does not add a second starter dataset. The first run against an empty labeled table invokes the existing `model/generate_data.py` script through the wrapper.

### Append synthetic data

Choose `operation: add-data` and set:

| Input | Allowed values | Default |
|---|---|---|
| `mode` | `normal` or `drift` | `normal` |
| `rows` | integer from 1 to 5000 | `150` |
| `label_noise` | decimal from 0.0 to 0.5 | `0.0` |
| `seed` | optional integer | empty |

`normal` adds rows using the normal synthetic distribution. `drift` adds rows using the shifted distribution. A supplied seed makes the generated batch reproducible. The operation is append-only and does not trigger training automatically.

### Train and promote to staging

Choose `operation: train`. The workflow:

1. creates the training Job using the immutable `ml-jobs` image tag;
2. runs the training pod only on the `training-spot` NodePool (`workload=training` selector and toleration);
3. waits for the Job, with the Job's 1200-second active deadline and a small status-propagation margin;
4. creates and waits for the promotion Job only after training succeeds;
5. inspects the promotion decision and restarts `staging/serving-api` only when the candidate was accepted.

If Spot capacity is unavailable, the Job times out and the workflow fails. There is no on-demand fallback. A Spot interruption can result in more than one registered model version; promotion selects the newest registered version after the successful training Job. Keep training runs serialized through this workflow.

Promotion compares the candidate's stored `f1_macro` against the current `@production` model. The first successful run, when no production alias exists, initialises `@staging` only. A candidate greater than or equal to the production score may update `@staging`; a lower-scoring candidate is rejected and leaves the existing staging alias and running staging Deployment unchanged. Production is never modified by this workflow.

The training code's existing train/test split runs over the accumulated labeled rows. The score comparison is therefore a simple demonstration metric, not a fixed holdout-set evaluation. Do not compare scores as though the test data were unchanged between runs.

## Enable and verify staging serving

After the first promotion succeeds and the `@staging` alias exists, enable `applications.servingStaging.enabled` in `deploy/argocd/values.yaml` (or the current equivalent values source) and let Argo CD create the staging Application. Do not enable `applications.servingProduction.enabled` as part of Day 2.

Check Argo CD and the workload:

```bash
kubectl -n argocd get applications
kubectl -n staging get deployment,service,pods
kubectl -n staging rollout status deployment/serving-api --timeout=5m
```

The service is internal `ClusterIP` on port 8000. Use port-forwarding for a smoke test; do not expose health, readiness or metrics endpoints publicly:

```bash
kubectl -n staging port-forward service/serving-api 8000:8000
```

In another terminal:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
curl -fsS -X POST http://127.0.0.1:8000/predict \
  -H 'Content-Type: application/json' \
  -d '{"title":"Prepare release notes","description":"Summarise the deployment changes"}'
curl -fsS http://127.0.0.1:8000/metrics
```

The prediction response must contain a `priority` (`low`, `medium` or `high`) and the exact loaded MLflow model version. `/health` tests process liveness; `/ready` remains unavailable until the alias-selected model has loaded. `/metrics` includes request count, prediction duration and loaded model version.

If promotion succeeds before the staging Application is enabled, the workflow reports that the alias is ready and skips the Deployment restart. Enable the Application, then wait for Argo CD to create a Ready Deployment. For an already deployed staging API, the workflow restarts it only after an accepted promotion and waits for rollout completion.

## MLflow verification

MLflow is internal-only. Use a port-forward to inspect the UI and Model Registry:

```bash
kubectl -n mlflow port-forward service/mlflow 5000:5000
```

Open `http://127.0.0.1:5000`. Verify the `task-priority-classifier` experiment, logged `accuracy` and `f1_macro` metrics, registered model versions, and `staging` alias. New runs should use MLflow's proxied artifact URI (`mlflow-artifacts:/`); the tracking server writes artifacts under the existing S3 `mlflow/` prefix.

Training and serving clients use the internal MLflow tracking URI. They must not use an S3 artifact URI directly and must not have S3 permissions or AWS Pod Identity. The MLflow server is the workload that writes the proxied artifacts and uses the `mlflow` service account identity.

## Operational cautions and troubleshooting

- PostgreSQL passwords can contain URL-special characters. The Jobs wrapper constructs the SQLAlchemy URL with SQLAlchemy's URL builder; do not rebuild it by string concatenation or print it.
- RDS connections require TLS (`sslmode=require`). Confirm connectivity/configuration without printing environment variables or Secret data.
- Create or verify the MLflow experiment only after the tracking server is configured for proxied artifacts. Existing experiments retain the artifact location assigned when they were created.
- A pending training pod usually indicates Spot capacity is unavailable. Inspect scheduling events and the Job before retrying; do not alter it to run on the general on-demand pool.
- A retry after Spot interruption can register another model version. Check the promotion log's selected version and decision.
- Moving an MLflow alias does not update a model already loaded in a running serving process. The workflow's accepted-promotion restart is required; do not restart after a rejected promotion.
- An absent staging alias before the first successful training/promotion run is expected. Do not diagnose the not-yet-enabled staging API as a failed service.
- The MLflow Deployment must retain the Reloader annotation so a refreshed `mlflow-db-credentials` Secret restarts the tracking server. Follow `docs/runbooks/secret-rotation.md` for the approved rotation procedure.
- The production Gateway and related AWS Load Balancer Controller resources are templates only during Day 2. Their certificate and public subnet CIDRs come from Terraform outputs at deployment time; do not commit those values to Git or create the ALB manually.
- The intended production HTTPRoute is only `POST /predict` with an exact path match. `/health`, `/ready`, `/metrics`, `/docs` and catch-all paths must remain unexposed.
- If Argo CD rejects a resource, add only the specific missing resource kind to the `serving` AppProject whitelist. Do not broaden cluster permissions.
- Ephemeral data, training and promotion Jobs are created by GitHub Actions with `kubectl create`; they are deliberately outside Argo CD's persistent chart paths. Do not place them under `deploy/argocd` or use `kubectl apply` on an existing Job.

## Day 2 acceptance checklist

Record each item as passed only after the corresponding command or live evidence has been collected. A source-level unit test or successful Helm render does not replace a live cluster check.

- [ ] AC-D2-01 — `scripts/verify-foundation.sh` passes.
- [ ] AC-D2-02 — Argo CD root, database bootstrap, MLflow and ml-jobs Applications are Synced/Healthy; enable and verify serving-staging only after `@staging` exists; production remains inactive.
- [ ] AC-D2-03 — MLflow has one Ready Deployment replica and an internal ClusterIP Service on port 5000.
- [ ] AC-D2-04 — MLflow logs show successful RDS-backed startup and contain no credentials.
- [ ] AC-D2-05 — MLflow UI/API is reachable through port-forward only.
- [ ] AC-D2-06 — A training run uses `mlflow-artifacts:/` and the tracking server writes under the S3 `mlflow/` prefix.
- [ ] AC-D2-07 — MLflow uses its expected Pod Identity; training and serving have no AWS identity or direct S3 access.
- [ ] AC-D2-08 — Running seed twice inserts the starter data once and then skips safely.
- [ ] AC-D2-09 — Normal add-data inserts the requested number of additional rows.
- [ ] AC-D2-10 — Drift add-data uses the changed distribution; invalid row/noise bounds are rejected.
- [ ] AC-D2-11 — Training schedules only on a Spot node with the training selector and toleration.
- [ ] AC-D2-12 — A successful run logs `accuracy` and `f1_macro` and registers a new model version.
- [ ] AC-D2-13 — Promotion unit tests cover first-run staging, equality, improvement, regression and fail-closed metric/registry errors.
- [ ] AC-D2-14 — A live promotion logs the selected candidate and decision, updates only staging when accepted, and leaves production unchanged.
- [ ] AC-D2-15 — Staging serving Deployment and ClusterIP Service become Ready on port 8000.
- [ ] AC-D2-16 — A valid staging `POST /predict` returns a supported priority and the exact loaded model version.
- [ ] AC-D2-17 — Staging `/health`, `/ready` and `/metrics` behave as specified through port-forward.
- [ ] AC-D2-18 — Serving has no AWS identity, AWS credentials or database Secret mounts.
- [ ] AC-D2-19 — Production serving chart passes `helm lint` and `helm template` with runtime certificate/CIDR inputs, while its Argo CD Application remains inactive.
- [ ] AC-D2-20 — Rendered production routing exposes only exact `POST /predict` to `serving-api:8000`.
- [ ] AC-D2-21 — `mlflowops:deployers` can create/read the required Jobs and logs, but cannot read Secrets, exec/port-forward into pods or modify RBAC; Deployment patch is limited to staging/production.
- [ ] AC-D2-22 — A PR validation run uses no AWS credentials and performs no ECR push or Kubernetes access. The PR trigger is currently disabled in the workflow, so this item remains pending until that trigger is enabled and exercised.
- [ ] AC-D2-23 — A successful main-branch CI run completes all intended blocking gates before publishing the three images with full Git-SHA tags; no `latest` tag is used. The current Trivy image `exit-code: "0"` mismatch must be resolved before claiming the image vulnerability scan is a blocking gate.
- [ ] AC-D2-24 — Manual `ML jobs` runs create unique Jobs, validate inputs, wait with timeouts, capture logs and use only the existing limited RBAC.
- [ ] AC-D2-25 — The image build helper uses the three real image Dockerfiles, contains no reference to `images/placeholder/`, and all three images build successfully.

## Day 3 hand-off

Day 3 can consume these stable interfaces: model aliases `staging` and `production`; a single serving image shared by environments; the production chart and Gateway API templates; the staging serving API's `/metrics`, JSON logs and health probes; and the MLflow registry plus the existing RDS/S3 artifact path.

The Day 3 production workflow still needs to wait for successful staging validation, request GitHub Environment approval, update `@production`, activate/sync the production serving Application, restart production and verify the public prediction route. That workflow is not part of this Day 2 runbook and must not be simulated by manually changing the production alias.

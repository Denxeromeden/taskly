# Ephemeral workload Job templates

This directory holds run-specific data, training and promotion Jobs created by
the GitHub Actions workflow. It is deliberately outside `deploy/argocd` and the
persistent Helm chart source paths, so Argo CD pruning cannot remove a running
or completed workflow Job.

The templates are `seed.yaml`, `add-data.yaml`, `train.yaml`, `promote.yaml`,
`promote-production.yaml` and `staging-smoke.yaml`. The workflow substitutes the
unique Job name and immutable ML jobs image tag, then creates each Job with
`kubectl create -f -`.

The workflow must:

- validate dispatch inputs before creating a Job;
- use a unique Job name for every workflow run;
- use a Git-SHA image from the latest successful main-branch image build;
- wait for completion with an explicit timeout and capture Job logs;
- use only the existing deployer RBAC permissions; it must not read Secrets,
  exec into pods, port-forward, or apply persistent chart resources.

The `deploy/charts/ml-jobs` Argo CD Application owns only the Job ServiceAccounts
and their base configuration. It does not own these run-specific Jobs.

After training and promotion complete successfully, the workflow restarts the
staging serving Deployment only when the promotion decision is
`INITIAL_STAGING_CANDIDATE` or `PROMOTED_TO_STAGING`. It does not restart staging
when the metric regresses or promotion fails. If the initial staging alias has
been created but the staging Deployment has not yet been enabled in Argo CD, the
workflow reports that state and leaves creation to Argo CD.

`staging-smoke.yaml` runs `jobs/smoke_staging.py` in the ml-jobs namespace with
the no-AWS-identity `training` service account. It checks `/ready`, submits one
synthetic `/predict` request, and verifies the response version matches the
concrete version behind the MLflow `@staging` alias. The staging serving
NetworkPolicy allows only pods labelled `app.kubernetes.io/name=staging-smoke`
from the `ml-jobs` namespace, so the check needs no `pods/exec` or
`pods/portforward` access. A failed smoke Job must stop the staging phase before
any production approval.

`promote-production.yaml` runs `jobs/promote_production.py` only after the
protected `production` GitHub Environment approves the release. It resolves the
concrete version behind `@staging`, re-reads `f1_macro` for that version and the
current `@production` version, and moves `@production` only when the candidate
still matches or exceeds production. Missing metrics fail closed, a lower score
leaves both aliases unchanged, and a repeat run on the same version is a
successful no-op. The Job uses the `training` service account and never reads
Secrets or task text.

Do not write a concrete MLflow model version into a Helm values file or
Deployment manifest; the serving process resolves the configured model alias
at startup.

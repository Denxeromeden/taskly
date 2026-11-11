# Ephemeral workload Job templates

This directory holds run-specific data, training and promotion Jobs created by
the GitHub Actions workflow. It is deliberately outside `deploy/argocd` and the
persistent Helm chart source paths, so Argo CD pruning cannot remove a running
or completed workflow Job.

The templates are `seed.yaml`, `add-data.yaml`, `train.yaml` and `promote.yaml`.
The workflow substitutes the unique Job name and immutable ML jobs image tag,
then creates each Job with `kubectl create -f -`.

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

Do not write a concrete MLflow model version into a Helm values file or
Deployment manifest; the serving process resolves the configured model alias
at startup.

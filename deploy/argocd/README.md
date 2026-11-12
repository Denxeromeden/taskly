# Argo CD root and child Applications

This directory is a Helm chart rendered by the `mlflowops-root` Application.
The root Application receives `repoURL` and `targetRevision` from the
`argocd-bootstrap` chart and passes them as Helm parameters to this chart.
Every child Application uses those shared values for its source repository
and revision.

The `mlflow` and `ml-jobs` Applications are rendered by default. The
`serving-staging` and `serving-production` Applications are prepared as
conditional templates and are not rendered unless their corresponding
`applications.*.enabled` value is set to `true` in this chart's `values.yaml`.

Enable `applications.servingStaging.enabled` only after the first successful
training/promotion run has created the `@staging` alias. The protected
`ML lifecycle` production release enables `applications.servingProduction` on
the first approved release; do not enable it manually.

## GitOps ownership

The root chart renders Argo CD `Application` resources only. It must not render
workload Deployments, Services, ServiceAccounts, NetworkPolicies, Gateway
resources, HPAs or the data/training/promotion Jobs.

Argo CD owns the persistent resources rendered by the child workload charts:

- `mlflow` owns the MLflow Deployment, Service and ServiceAccount.
- `ml-jobs` owns the Job service accounts. Its chart does not own the
  run-specific seed, add-data, training or promotion Jobs.
- `serving-staging` and `serving-production` own their respective serving
  Deployments, Services, ServiceAccounts, NetworkPolicies, HPAs and Gateway
  resources when enabled by their environment values.

GitHub Actions owns the ephemeral data, training and promotion Jobs. The
workflow creates each Job through the Kubernetes API from templates outside
this directory, using unique Job names. It must not deploy or patch persistent
chart resources through Helm, or apply a second copy of those resources.

The existing `db-bootstrap` Application is an intentional exception: its
separate `deploy/charts/db-bootstrap` chart manages the database bootstrap
Job as an Argo CD `Sync` hook with `HookSucceeded` cleanup. It is not a
GitHub Actions data/training/promotion Job.

## Staging model refresh

After a promotion Job succeeds and reports that the candidate was accepted,
the workflow restarts the Argo CD-owned staging Deployment and waits for the
rollout:

```bash
kubectl -n staging rollout restart deployment/serving-api
kubectl -n staging rollout status deployment/serving-api --timeout=5m
```

A failed or rejected promotion must not restart staging. The restart is a
runtime refresh only; it does not change the chart's desired image,
configuration or model alias. The staging Application ignores only the
`kubectl.kubernetes.io/restartedAt` Deployment pod-template annotation and
uses `RespectIgnoreDifferences=true`, preventing automated self-heal from
reverting the restart marker. All other Deployment fields remain managed by
Argo CD.

## Production activation, refresh and rollback

On the first approved release, the protected `ML lifecycle` workflow moves
`@production`, commits only `applications.servingProduction.enabled: true`, and
waits for Argo CD to create the production Deployment. Argo CD uses the same
immutable serving image tag already tested in staging and the serving process
loads the concrete version behind `@production`. Branch protection must allow
the workflow's `contents: write` token to push that one-file activation commit;
the workflow retries a rebase/push up to three times and fails closed otherwise.

On later releases, and after an approved rollback, the workflow restarts the
production Deployment and waits for its desired replicas to become ready:

```bash
kubectl -n production rollout restart deployment/serving-api
kubectl -n production rollout status deployment/serving-api --timeout=10m
```

The production Application ignores the same `kubectl.kubernetes.io/restartedAt`
annotation and uses `RespectIgnoreDifferences=true`. Automated sync, self-heal
and prune remain enabled; do not disable GitOps ownership to make restarts work.
The subsequent public `POST /predict` check must return the exact version behind
`@production`. Because the showcase certificate may be self-signed, external
checks and the in-cluster verification Job use the equivalent of `curl -k`.

Emergency rollback is a protected `production-rollback` workflow dispatch with
an existing `model_version` input. It validates the version, sets
`@production` through an in-cluster Job, restarts the production Deployment and
checks the public prediction response. Do not edit the alias or Deployment
imperatively outside that workflow.

The serving chart selects the model by logical alias (`staging` or
`production`). Do not commit concrete MLflow model-version numbers into Helm
values or Kubernetes deployment manifests.

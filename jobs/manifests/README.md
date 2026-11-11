# Ephemeral workload Job templates

This directory holds templates for run-specific data, training and promotion
Jobs created by the GitHub Actions workflow. It is deliberately outside
`deploy/argocd` and outside the source paths of the persistent Helm charts, so
Argo CD pruning cannot remove a running or completed workflow Job.

The current templates are `train.yaml` and `promote.yaml`. Add seed and
add-data Job templates here when those workflow operations are wired. Do not
move these templates under `deploy/argocd` or a chart managed by an Argo CD
Application.

The workflow must:

- validate inputs and substitute the template placeholders before creating a
  Job;
- use a unique Job name for every workflow run;
- create Jobs through the Kubernetes API (for example, `kubectl create -f -`),
  not by applying updates to a previous Job;
- wait for completion with an explicit timeout and capture the Job logs;
- use only the existing deployer RBAC permissions; it must not read Secrets,
  exec into pods, or apply persistent chart resources.

The `deploy/charts/ml-jobs` Argo CD Application owns only the Job ServiceAccounts
and their base configuration. It does not own these run-specific Jobs.

After training and promotion complete successfully, restart the staging serving
Deployment only when the promotion result says the candidate was accepted:

```bash
kubectl -n staging rollout restart deployment/serving-api
kubectl -n staging rollout status deployment/serving-api --timeout=5m
```

Do not restart staging when training or promotion fails, or when promotion
rejects the candidate. Do not write a concrete MLflow model version into a
Helm values file or Deployment manifest; the serving process resolves the
configured model alias at startup.

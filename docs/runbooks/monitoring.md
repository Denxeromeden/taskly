# Monitoring and log operations

This runbook covers the internal monitoring stack managed by Argo CD. Prometheus, Alertmanager,
Grafana, Loki, Alloy and Pushgateway are deployed in the existing `monitoring` namespace. None of
their Services is public. The only public application route remains HTTPS `POST /predict` in the
`production` namespace.

## Ownership and deployed components

The `monitoring` Argo CD Application owns the monitoring wrapper chart at
`deploy/charts/monitoring`. Do not run `helm install`, `helm upgrade` or `helm uninstall` against
these workloads. Change the chart or values in Git and let Argo CD reconcile them.

The wrapper chart pins kube-prometheus-stack `92.14.1`, Loki `18.5.0`, Alloy `1.15.0` and
Prometheus Pushgateway `3.9.0` in `Chart.yaml` and `Chart.lock`. Prometheus stores seven days of
metrics on a 10 GiB `gp3` PVC. Loki runs as one monolithic instance and stores seven days of logs
on its own 10 GiB `gp3` PVC. Neither component uses the MLflow artifact bucket or receives AWS
permissions. Pushgateway keeps one stable drift metric group on a 2 GiB `gp3` persistent volume; a new CronJob run replaces that
group instead of creating per-run series. The StatefulSet mounts the persisted data file and flushes it at one-minute intervals.

The Prometheus Operator is the owner of the serving ServiceMonitors and PrometheusRule. Each
serving ServiceMonitor selects only the `serving-api` Service in its own namespace and scrapes
`/metrics` on the named `http` port every 30 seconds. NetworkPolicy permits ingress from the
`monitoring` namespace to the serving port; the staging smoke Job and production ALB subnet rules
remain separate.

## Provision the Grafana credential and GitOps permissions

The monitoring stack depends on one platform Terraform change: a random Grafana administrator
password is stored in Secrets Manager, and the platform AppProject is extended for the exact
cluster-scoped resource kinds created by the pinned monitoring chart. The
`argocd-bootstrap` chart version is bumped so Terraform recognizes the AppProject template change.

From the repository root, run the normal initialization for the existing platform stack so the
new, exactly pinned `hashicorp/random` provider is installed, then inspect the plan:

```bash
terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform plan
```

Review the plan before applying it. It should add the random password, the
`mlflowops/monitoring/grafana-admin` Secrets Manager secret and its version, and upgrade the
`argocd-bootstrap` Helm release in place. It should not replace the cluster or change unrelated
network, database, IAM or storage resources. Apply only after confirming the plan matches those
expected changes. No manual Secret creation or Helm command is needed.

After the platform update, allow External Secrets and Argo CD to reconcile. Verify that
`monitoring-grafana-credentials` is Ready before using Grafana. Do not print the Secret value into
shared logs or terminal transcripts.

## Access Grafana

Use a local port-forward; no ingress, Gateway, LoadBalancer or NodePort is configured:

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
```

Open `http://localhost:3000`. The dashboard sidecar provisions `MLFlowOps Overview` from the
version-controlled ConfigMap. Terraform creates a random 40-character password in AWS Secrets Manager at
`mlflowops/monitoring/grafana-admin`. External Secrets syncs it to the
`monitoring-grafana-credentials` Kubernetes Secret, and Grafana reads the
`admin-user` / `admin-password` keys from that Secret. The random password is in encrypted
Terraform state and Secrets Manager, not in Helm values or Git. Retrieve credentials only from a
trusted operator workstation when needed:

```bash
kubectl -n monitoring get secret monitoring-grafana-credentials \
  -o jsonpath='{.data.admin-user}' | base64 -d; echo
kubectl -n monitoring get secret monitoring-grafana-credentials \
  -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

The Prometheus datasource is provisioned by kube-prometheus-stack. Loki is provisioned as the
`Loki` datasource. Dashboard panels use the existing serving metrics and Kubernetes state metrics,
and include queries for drift metrics when the drift workload publishes them.

## Inspect Prometheus and alerts

Inspect the monitoring Application and workloads:

```bash
kubectl -n argocd get application monitoring
kubectl -n monitoring get pods,services,pvc
kubectl -n monitoring get externalsecret monitoring-grafana-credentials
kubectl -n monitoring get servicemonitors,prometheusrules
```

The Prometheus web UI is internal. When direct access is useful, find the Prometheus Service and
port-forward it locally:

```bash
kubectl -n monitoring get services | grep prometheus
# Use the Prometheus Service name shown above.
kubectl -n monitoring port-forward svc/<prometheus-service-name> 9090:9090
```

Open `http://localhost:9090`. Check **Status → Targets** for both staging and production
`serving-api` targets, plus Pushgateway. The expected serving series include:

- `taskly_prediction_requests_total`, split by `model_version` and `outcome`;
- `taskly_prediction_request_duration_seconds_bucket` for latency quantiles;
- `taskly_model_info` for the loaded model version.

The custom alert rules cover sustained prediction errors, unavailable serving replicas,
CrashLoopBackOff/repeated restarts, OOM termination, detected drift, stale drift runs and an
unavailable Pushgateway scrape. Alertmanager runs internally and has no external receiver
configured. Check alerts in Prometheus **Alerts** or through the internal Alertmanager Service.

The Pushgateway alert is based on Prometheus' `up` series for its `/metrics` scrape. This verifies
that Prometheus can scrape the metrics endpoint; it does not scrape Pushgateway's separate
`/-/healthy` probe endpoint.

The seven rule names and firing conditions are:

| Alert | Condition | Severity |
|---|---|---|
| `ServingHighPredictionErrorRate` | Error ratio above 5% for 5 minutes, with a minimum request-rate guard | warning |
| `ServingDeploymentUnavailable` | Desired serving replicas exceed available replicas for 5 minutes | critical |
| `ServingPodCrashLooping` | Container is in `CrashLoopBackOff` or has more than two restarts in 10 minutes | warning |
| `ServingContainerOOMKilled` | Last serving-container termination reason is `OOMKilled` | critical |
| `DriftDetected` | Latest drift gauge is 1 for 5 minutes | warning |
| `DriftJobStale` | An attempt has been recorded but no successful drift run has completed for 26 hours | warning |
| `DriftPushgatewayUnavailable` | Pushgateway `/metrics` scrape is down or its target is absent | warning |

The drift rules remain inactive until the drift workload publishes the corresponding stable metric
group. In particular, `DriftJobStale` needs an initial recorded attempt before it can identify a
missed subsequent run.

## Query logs in Loki

The Alloy collector uses Kubernetes API pod discovery and pod-log reads. It is restricted to
`mlflow`, `ml-jobs`, `staging`, `production` and `monitoring`. Its namespace-scoped Roles grant only
`get/list/watch` on Pods and `get` on `pods/log`. The collector does not read Secrets, modify
workloads, mount host log directories, access the container runtime socket or run privileged.

In Grafana Explore, select the `Loki` datasource. Useful starting queries are:

```logql
{namespace="staging", app="serving-api"} | json
{namespace="production", app="serving-api"} | json
{namespace="mlflow"} | json
{namespace="ml-jobs"} | json
```

Only bounded metadata labels are retained: namespace, app, container and level. JSON fields remain
available for filtering but request/task identifiers and arbitrary fields are not promoted to
Loki labels. The serving API logs model version, request path/status, duration and error type; it
does not log request title or description. Do not add payloads, credentials, task identifiers or
raw error text to application logs.

The API-based collector avoids node filesystem access, but it generates Kubernetes API and
kubelet traffic as it discovers Pods and streams their logs. Review collector resource use and
API request volume if the number of pods increases.

## Verify the dashboard and alert rules

After the monitoring Application is Synced/Healthy and Prometheus Operator CRDs are established:

```bash
kubectl get crd servicemonitors.monitoring.coreos.com
kubectl -n staging get servicemonitor serving-api -o yaml
kubectl -n production get servicemonitor serving-api -o yaml
kubectl -n monitoring get prometheusrule taskly-serving-alerts -o yaml
```

If a serving Application was reconciled before the ServiceMonitor CRD existed, refresh and sync
that Application after the monitoring Application is healthy. Confirm both ServiceMonitors are
selected and both targets become `UP` before relying on dashboard request-rate or latency panels.

To exercise alert expressions without leaving a forced alert enabled, use Prometheus' expression
browser with controlled query inputs or a temporary synthetic metric in a test environment. Do not
leave test alerts or synthetic metric series in the live monitoring namespace.

## Storage, retention and cleanup

Prometheus retains seven days of metrics; Loki retains seven days of logs. Their PVCs use the
existing `gp3` StorageClass. Loki does not have S3 credentials or an object-store configuration.
The Pushgateway stable drift group persists across Job completion and remains until overwritten or
explicitly deleted. When the drift workflow is introduced, its runbook must document removal of
that group during cleanup.

Do not delete monitoring PVCs as routine cleanup. Deleting a PVC removes the corresponding local
metrics/log history. Normal changes should be made through Git and Argo CD; infrastructure and
namespace lifecycle remain under the existing Terraform/foundation ownership.

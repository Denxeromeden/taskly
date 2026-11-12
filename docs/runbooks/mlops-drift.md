# Drift detection operations

The drift detector is an Argo-managed CronJob in `ml-jobs`. It reads labeled rows from the
existing Tasks PostgreSQL database in a read-only transaction, resolves the concrete version
behind MLflow `@production`, and compares recent data with an older reference window.

## Data comparison

The current window is the newest 200 labeled rows. The reference window contains up to 2,000
older labeled rows. At least 50 rows are required in each window. The task table has no creation
timestamp, so ascending `id` is used as the append-order proxy.

Evidently compares six aggregate features: title character count, title word count, description
word count, combined text length, observed priority and production-model predicted priority.
The drift score is the share of these features flagged by the pinned PSI test (threshold 0.1).
A comparison is marked as drift when at least 30% of the features are flagged.

Raw title/description, task IDs and row-level predictions are not included in the report or logs.
The report contains aggregate distributions and is uploaded to the existing artifact bucket under
`drift-reports/YYYY/MM/DD/<job-name>/report.html`. The drift Pod Identity role is restricted to
that prefix and does not access MLflow artifacts or Secrets Manager.

## Metrics

Every attempt replaces one stable Pushgateway group: `job=taskly-drift-detection`. Metrics include
`taskly_drift_detected`, `taskly_drift_score`, row counts, last-run and last-success timestamps,
report-upload status, and `taskly_drift_run_status` (0 success, 1 insufficient data, 2 missing
production alias, 3 error). Skipped runs preserve the previous successful timestamp and drift
result so the stale-run and drift alerts retain their meaning.

Pushgateway data persists independently of completed Jobs. To remove the group during cleanup:

```bash
kubectl -n monitoring port-forward svc/monitoring-pushgateway 9091:9091
curl -X DELETE http://localhost:9091/metrics/job/taskly-drift-detection
```

## Run a controlled drift demonstration

First verify that the production alias exists and the monitoring stack is healthy. The CronJob is
initially suspended so it does not start before the first manual acceptance run.

Append a controlled drift batch using the existing `ML jobs` workflow (`add-data`, `mode=drift`,
150 rows, deterministic seed). Do not retrain or promote the model after adding the batch.

Run one Job from the Argo-managed CronJob:

```bash
kubectl -n ml-jobs create job --from=cronjob/ml-jobs-drift-detection drift-acceptance
kubectl -n ml-jobs logs -f job/drift-acceptance
kubectl -n ml-jobs get job drift-acceptance
```

Verify that a report object was uploaded under `drift-reports/`, and inspect the Grafana
`MLFlowOps Overview` panels for drift status, score and last successful run. Check the Prometheus
target for Pushgateway and the `DriftDetected` / `DriftJobStale` alerts.

After the manual run succeeds, change `drift.suspend` to `false` in
`deploy/charts/ml-jobs/values.yaml` and let Argo CD reconcile. The CronJob then runs every six
hours. Do not create a separate persistent Job or CronJob outside Argo CD.

If there are too few labeled rows or no production alias, the job records a skipped status. It does
not report a clean/no-drift result. A failed comparison or report upload exits non-zero.

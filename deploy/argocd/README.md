# Argo CD child Applications

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
training/promotion run has created the `@staging` alias. Keep
`applications.servingProduction.enabled` set to `false` until the Day 3
production approval wiring is implemented.

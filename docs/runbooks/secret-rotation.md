# Secret rotation runbook

The `mlflowops/rds/mlflow` and `mlflowops/rds/tasks` secrets are rotated by AWS's
`SecretsManagerRDSPostgreSQLRotationSingleUser` Serverless Application Repository function every
30 days. The Lambda runs in the private subnets and its security group is allowed to reach RDS on
5432 and the Secrets Manager regional HTTPS endpoint on 443 through the VPC NAT gateway.

Manual rotation:

```bash
aws secretsmanager rotate-secret --secret-id mlflowops/rds/mlflow
aws secretsmanager describe-secret --secret-id mlflowops/rds/mlflow \
  --query '{Name:Name,LastRotatedDate:LastRotatedDate,RotationEnabled:RotationEnabled}'
```

External Secrets refreshes synced Kubernetes Secret values hourly. Stakater Reloader watches
annotated workloads to restart consumers after the Kubernetes Secret changes. Verify the application
reconnects and that the previous password no longer authenticates. Never print or paste secret values
into tickets, terminal logs, or evidence files. The initial passwords are in the encrypted Terraform
state until the first rotation; rotate both app secrets after database bootstrap.

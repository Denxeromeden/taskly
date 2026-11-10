# Bootstrap state backend

This stack is intentionally initialized with local state on the first run. It creates the versioned,
private, TLS-only S3 bucket and KMS key used by the `infra` and `platform` states.

```bash
cd terraform/bootstrap
terraform init
terraform plan -out bootstrap.tfplan
# Review the plan, then apply it manually:
terraform apply bootstrap.tfplan
```

Do not destroy this stack during normal teardown. Keep its state local in a secure location and back
it up; the bucket it creates stores the other two Terraform states with S3-native locking.

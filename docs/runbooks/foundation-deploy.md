# Foundation deployment runbook

1. Confirm AWS CLI credentials, sufficient IAM permissions (including KMS grant permissions for ECR), Terraform 1.17.1, Helm 3, kubectl,
   Git and access to the GitHub repository.
2. Update `terraform/infra/terraform.tfvars` (especially `admin_principal_arns`). The demo keeps
   `eks_public_access_cidrs = ["0.0.0.0/0"]` so standard GitHub-hosted runners can reach the public
   Kubernetes API; narrow it when using a runner with stable egress. Copy
   `terraform/platform/terraform.tfvars.example` for platform overrides. When deploying a
   fork, update both `argocd_repo_url` and `deploy/argocd/db-bootstrap-application.yaml`.
3. Run `terraform -chdir=terraform/bootstrap init`, review the plan, and apply the bootstrap stack.
   Keep its local state securely backed up.
4. Run `scripts/init-backends.sh`; initialize the infra stack using its `backend.hcl`.
5. Review `terraform -chdir=terraform/infra plan`. Do not apply until a human approves the plan. If the account already has the EC2 Spot service-linked role, the Terraform data lookup detects it and skips creation.
6. After infra is applied and the API endpoint is reachable, initialize and plan `terraform/platform`.
   The platform apply uses AWS CLI and `kubectl` to install the pinned Gateway API CRDs, so both must
   be available wherever Terraform runs. Review the generated self-signed ACM certificate or provide
   a trusted certificate ARN.
7. After platform apply, check NodePools, the `aws-alb` GatewayClass (`Accepted=True`), Gateway API
   and AWS Gateway extension CRDs, all three ExternalSecrets, Argo CD root Application and the
   `db-bootstrap` Application. Confirm the bootstrap hook completed successfully before testing DB access.
8. Force the first rotation of each application credential (the random initial passwords remain in
   encrypted Terraform state until this is done):
   ```sh
   aws secretsmanager rotate-secret --region eu-central-1 --secret-id mlflowops/rds/mlflow
   aws secretsmanager rotate-secret --region eu-central-1 --secret-id mlflowops/rds/tasks
   ```
   Wait for External Secrets to refresh, then verify login with each current secret from a throwaway
   `postgres:16-alpine` pod using `sslmode=require`. `mlflow_app` must connect to `mlflow` and be
   denied access to `tasks`; `tasks_app` must connect to `tasks` and be denied access to `mlflow`.
   For AC-14, also verify the previous `AWSPREVIOUS` password fails after rotation without printing
   either credential to terminal output or CI logs.
9. Populate the three ECR repositories with the clearly marked non-production placeholders using
   `scripts/build-and-push-images.sh`. Replace them with workload images before deploying any app.
10. Run `scripts/verify-foundation.sh` and the evidence scripts. Save outputs only after reviewing
    them for secret values and account details.

The verifier checks the cluster version/endpoints, base-node count, add-on/controller readiness,
Gateway API CRDs and the accepted `aws-alb` GatewayClass, NodePool existence, ClusterSecretStore and all three ExternalSecrets, Argo CD root and database-bootstrap
Application health, S3 controls, and RDS placement/encryption. It
does not trigger Karpenter scale-out or Spot provisioning: run the AC-05/AC-06 throwaway workload
experiments from `FoundationDesign.md` manually in a live cluster, then remove those workloads.
The rotation evidence script records rotation timestamps without printing credentials; verify
current/old-password database login behavior separately before accepting AC-14.
Terraform plans are safe to review; `apply` and `destroy` require explicit human approval.

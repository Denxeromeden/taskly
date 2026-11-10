# Foundation threat model

| Threat | Mitigation | Residual risk / evidence |
|---|---|---|
| Stolen CI credentials | GitHub OIDC only; trust scoped to repository main branch and staging/production environments; no long-lived AWS keys | Environment protection remains a GitHub repository control. Review `aws iam get-role` trust policy. |
| Compromised CI pipeline | ECR actions scoped to three repositories; `eks:DescribeCluster` only; Kubernetes group has namespace-scoped Job/deployment and pod-log permissions | CI can launch Jobs in `ml-jobs`; review image provenance and use SHA tags. |
| Compromised workload pod | Only MLflow, drift and External Secrets receive Pod Identity; MLflow role constrained to `mlflow/` prefix | Pods share node SG; IAM and NetworkPolicy contain workload access. |
| Lateral movement | Default-deny ingress in workload namespaces; restricted Pod Security; non-root, dropped capabilities, RuntimeDefault seccomp | NetworkPolicy enforcement requires VPC CNI network policy enabled and tested. |
| Direct database access from internet | RDS private in isolated subnets, not public; SG allows port 5432 only from EKS node SG and rotation Lambda SG; TLS required | Pods share node SG; database-level credentials and per-database CONNECT grants are the additional boundary. |
| Credential disclosure | Secrets Manager + KMS, ESO sync, 30-day rotation, Reloader | Initial application passwords exist in encrypted Terraform state until first successful rotation. Perform initial rotation promptly. |
| Artifact exposure | S3 Block Public Access, TLS-only bucket policy, versioning, SSE-KMS and prefix-constrained Pod Identity | Audit and artifacts lifecycle is configured for a demo retention period of 30 days. |
| Malicious or suspicious API activity | CloudTrail management events, log-file validation, GuardDuty, Athena denied-call queries, EKS audit logs | Alerting/monitoring of findings is an operational responsibility. |
| API endpoint exposure | EKS IAM authentication with public + private API endpoint; configurable public CIDRs | Default `0.0.0.0/0` is an accepted demo risk; restrict to operator CIDRs before production. |
| Image supply-chain compromise | Immutable ECR tags, scan on push, pinned base versions, downstream Trivy gate for CRITICAL findings | The foundation provisions repositories; full image build and promotion pipelines belong to later layers. |
| Control-plane / state compromise | Terraform state in versioned private S3 bucket, SSE-KMS, TLS-only policy and native S3 lockfile | Bootstrap local state requires secure backup and workstation hygiene. |

## Security boundaries

- Human administrators receive cluster-admin through EKS access entries; Terraform's creating principal
  receives creator-admin during cluster creation.
- GitHub Actions gets an EKS access entry for group `mlflowops:deployers` and no EKS access policy.
- `serving`, `training`, `seed`, `add-data` and `db-bootstrap` service accounts have no AWS role association.
- The rotation Lambda can reach RDS on 5432 and Secrets Manager on 443 through the private subnet's NAT path.
- The ALB is the intended public application entry point; no public database or NodePort is created.

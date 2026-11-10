# Pinned tool and component versions

Verified on **2026-11-09**. Recheck compatibility before upgrading; this file records chosen pins,
not a promise that every chart was installed in this execution environment. Workload-layer pins
were resolved on **2026-11-10** and smoke-tested on Python 3.12.

| Component | Pin | Verification |
|---|---:|---|
| Terraform CLI | 1.17.1 (recommended workstation version) | HashiCorp release published 2026-10-28 |
| AWS provider | 6.72.0 | Registry release published 2026-11-04 |
| EKS Terraform module | 21.29.0 | Module release 2026-10-07 |
| AWS Load Balancer Controller Helm chart/app | 3.6.2 | Upstream release published 2026-10-25; Gateway API support documented for v1.6.0 |
| Kubernetes Gateway API standard CRDs | v1.6.0 | Version the AWS Load Balancer Controller is built for |
| AWS Load Balancer Controller Gateway API extension CRDs | v3.6.2 | Pinned to the matching controller release tag |
| Karpenter Helm chart | 1.15.1 | Upstream stable 1.x release |
| External Secrets Helm chart | 2.14.0 | Upstream chart release |
| Argo CD Helm chart | 10.17.0 | Upstream chart version |
| Stakater Reloader Helm chart | 2.2.19 | Upstream chart release 2026-10-27 |
| Metrics Server Helm chart | 3.14.0 (app 0.9.1) | Upstream chart 3.14.0; app 0.9.1 supports Kubernetes 1.34+ |
| Kubernetes / EKS | 1.35 | AWS EKS 1.35 standard support; see AWS EKS release calendar |
| PostgreSQL | 16 (latest available RDS minor selected by AWS) | Major version pinned to project compose compatibility |
| postgres bootstrap image | 16-alpine | Major version aligned with RDS |
| Terraform random provider | 3.9.1 | Terraform Registry provider pin |
| Helm provider | 3.3.0 | Terraform Registry provider pin |
| Kubernetes provider | 3.4.0 | Terraform Registry provider pin |
| TLS provider | 4.4.2 | Terraform Registry provider pin |
| Python base image (MLflow image) | `python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` | Docker Hub multi-arch index digest resolved 2026-11-10 |
| MLflow (tracking server image) | 2.22.5 | Latest stable 2.x on PyPI on 2026-11-10; satisfies `mlflow>=2.10,<3` from `model/requirements.txt` |
| SQLAlchemy (tracking server image) | 2.0.57 | Latest 2.0.x on PyPI; 2.1 removes `sqlalchemy.pool.FallbackAsyncAdaptedQueuePool`, which mlflow 2.22.5 imports at startup |
| psycopg2-binary (tracking server image) | 2.9.13 | Latest on PyPI 2026-11-10; cp312 manylinux wheel published |
| boto3 (tracking server image) | 1.43.127 | Latest on PyPI 2026-11-10; py3-none-any wheel published |

Karpenter EC2NodeClass uses the design-approved `al2023@latest` alias. The exact AMI resolved by AWS
is therefore a runtime value and must be recorded in the deployment evidence after the first apply.
EKS managed add-ons use `most_recent = true` at cluster creation; capture the resolved versions with
`aws eks describe-addon-versions` / `aws eks describe-addon` and add them below after deployment.

## Official sources checked

- Terraform: https://github.com/hashicorp/terraform/releases
- AWS provider: https://github.com/hashicorp/terraform-provider-aws/releases
- EKS module: https://github.com/terraform-aws-modules/terraform-aws-eks/releases
- Karpenter: https://github.com/kubernetes-sigs/karpenter/releases
- AWS Load Balancer Controller: https://github.com/kubernetes-sigs/aws-load-balancer-controller/releases
- AWS Load Balancer Controller Gateway API guide: https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/gateway/gateway/
- Kubernetes Gateway API: https://github.com/kubernetes-sigs/gateway-api/releases
- External Secrets: https://github.com/external-secrets/external-secrets/releases
- Argo CD: https://github.com/argoproj/argo-cd/releases
- Reloader: https://github.com/stakater/Reloader/releases
- Metrics Server: https://github.com/kubernetes-sigs/metrics-server/releases
- MLflow: https://pypi.org/project/mlflow/2.22.5/
- SQLAlchemy: https://pypi.org/project/SQLAlchemy/2.0.57/
- psycopg2-binary: https://pypi.org/project/psycopg2-binary/2.9.13/
- boto3: https://pypi.org/project/boto3/1.43.127/
- Python base image: https://hub.docker.com/_/python

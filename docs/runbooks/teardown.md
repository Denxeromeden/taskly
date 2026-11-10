# Foundation teardown runbook

Run `scripts/teardown.sh` first. It removes Gateway API HTTPRoutes/GRPCRoutes, Gateways,
AWS Gateway extension configuration objects, LoadBalancer Services, PVCs, Argo CD child Applications
and Karpenter NodePools so controller-created AWS resources can be released. It does
not invoke Terraform destroy. Review and explicitly approve destruction of `terraform/platform`,
then `terraform/infra`. Keep `terraform/bootstrap` and ECR repositories for a rebuild unless a full
account cleanup has been approved. Confirm ALBs, target groups, Karpenter EC2 instances, EBS volumes,
NAT gateway and RDS charges are gone before considering teardown complete.

After Terraform teardown, run `scripts/verify-clean.sh`. It checks tagged resources; also review the AWS console/CLI for untagged EBS volumes and ENIs before considering the account clean.

# Infra stack

Creates the AWS foundation resources in dependency order: CMKs, network, EKS, data services,
GitHub/Pod Identity, and the audit baseline. It uses the S3 backend created by `bootstrap`.

After bootstrap, run `scripts/init-backends.sh` to generate the local `backend.hcl` files, then
initialize with `terraform init -backend-config=backend.hcl`. Review `terraform plan` carefully.
Never apply/destroy without explicit human approval. Public EKS endpoint CIDRs default to
`0.0.0.0/0` per the demo design; restrict them before deployment where possible.

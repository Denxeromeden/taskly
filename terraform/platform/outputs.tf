output "certificate_arn" { value = local.certificate_arn }
output "certificate_is_self_signed" { value = var.acm_certificate_arn == null }
output "argocd_url" { value = "kubectl -n argocd port-forward svc/argo-cd-argocd-server 8080:443" }

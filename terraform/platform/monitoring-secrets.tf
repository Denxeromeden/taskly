resource "random_password" "grafana_admin" {
  length           = 40
  special          = true
  override_special = "!#$%&()-_=+[]{}<>:?"
}

# Use Secrets Manager default encryption; the existing External Secrets identity already has read access to the mlflowops/* prefix.
resource "aws_secretsmanager_secret" "grafana_admin" {
  name        = "mlflowops/monitoring/grafana-admin"
  description = "Grafana administrator credentials for the internal monitoring stack"
  tags = {
    Project   = var.project
    Env       = var.environment
    ManagedBy = "Terraform"
  }
}

resource "aws_secretsmanager_secret_version" "grafana_admin" {
  secret_id     = aws_secretsmanager_secret.grafana_admin.id
  secret_string = jsonencode({
    "admin-user"     = "admin"
    "admin-password" = random_password.grafana_admin.result
  })
}

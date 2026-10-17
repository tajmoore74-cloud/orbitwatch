output "namespace" {
  value = kubernetes_namespace.app.metadata[0].name
}

output "app_release" {
  value = "${helm_release.orbitwatch.name} (${helm_release.orbitwatch.status})"
}

output "grafana_admin_password" {
  description = "Read it with: terraform output -raw grafana_admin_password"
  value       = local.grafana_password
  sensitive   = true
}

output "try_it" {
  value = "kubectl -n ${var.namespace} port-forward svc/orbitwatch 8080:80 && curl localhost:8080/telemetry/99901"
}

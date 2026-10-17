locals {
  # Resolve the chart relative to this module, so it works from any directory
  chart_path = coalesce(var.chart_path, "${path.module}/../../charts/orbitwatch")

  grafana_password = (
    var.grafana_admin_password != "" ? var.grafana_admin_password : random_password.grafana.result
  )
}

resource "random_password" "grafana" {
  length  = 24
  special = false
}

# ---- Namespace with guardrails ---------------------------------------------

resource "kubernetes_namespace" "app" {
  metadata {
    name = var.namespace

    labels = {
      "app.kubernetes.io/part-of"          = "orbitwatch"
      "pod-security.kubernetes.io/enforce" = "restricted"
    }
  }
}

# Caps the total resources the namespace can use, so one app can't
# starve everything else on the cluster.
resource "kubernetes_resource_quota" "app" {
  metadata {
    name      = "orbitwatch-quota"
    namespace = kubernetes_namespace.app.metadata[0].name
  }
  spec {
    hard = {
      "requests.cpu"    = "1"
      "requests.memory" = "1Gi"
      "limits.cpu"      = "2"
      "limits.memory"   = "2Gi"
      "pods"            = "20"
    }
  }
}

# Default requests/limits for any container that forgets to set them.
resource "kubernetes_limit_range" "app" {
  metadata {
    name      = "orbitwatch-defaults"
    namespace = kubernetes_namespace.app.metadata[0].name
  }
  spec {
    limit {
      type = "Container"

      default = {
        cpu    = "250m"
        memory = "128Mi"
      }
      default_request = {
        cpu    = "50m"
        memory = "64Mi"
      }
    }
  }
}

# ---- Monitoring: Prometheus + Grafana --------------------------------------

resource "helm_release" "monitoring" {
  count            = var.enable_monitoring ? 1 : 0
  name             = "monitoring"
  namespace        = "monitoring"
  create_namespace = true
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = "66.3.1"

  # Keep it light enough for a small k3s node
  values = [file("${path.module}/../../monitoring/kube-prometheus-values.yaml")]

  # The password comes from a variable or is randomly generated. It never
  # appears in the repo, and Terraform hides it in plan output.
  set_sensitive {
    name  = "grafana.adminPassword"
    value = local.grafana_password
  }
}

# Grafana's sidecar finds this ConfigMap by its label and loads the dashboard.
resource "kubernetes_config_map" "dashboard" {
  count = var.enable_monitoring ? 1 : 0

  metadata {
    name      = "orbitwatch-dashboard"
    namespace = "monitoring"

    labels = {
      grafana_dashboard = "1"
    }
  }

  data = {
    "orbitwatch.json" = file("${path.module}/../../monitoring/orbitwatch-dashboard.json")
  }

  depends_on = [helm_release.monitoring]
}

# ---- Supply-chain policy: only signed images may run -------------------------

resource "helm_release" "kyverno" {
  count            = var.enable_image_signature_policy ? 1 : 0
  name             = "kyverno"
  namespace        = "kyverno"
  create_namespace = true
  repository       = "https://kyverno.github.io/kyverno/"
  chart            = "kyverno"
  version          = "3.3.4"
  wait             = true
}

resource "helm_release" "signature_policy" {
  count     = var.enable_image_signature_policy ? 1 : 0
  name      = "orbitwatch-policy"
  namespace = "kyverno"
  chart     = "${path.module}/../../charts/orbitwatch-policy"

  set {
    name  = "imageReference"
    value = "${var.image_registry != "" ? "${var.image_registry}/" : ""}${var.image_repository}*"
  }

  set {
    name  = "namespaces[0]"
    value = var.namespace
  }

  set_sensitive {
    name  = "cosignPublicKey"
    value = var.cosign_public_key
  }

  lifecycle {
    precondition {
      condition     = var.cosign_public_key != ""
      error_message = "enable_image_signature_policy requires cosign_public_key."
    }
  }

  depends_on = [helm_release.kyverno]
}

# ---- The app ---------------------------------------------------------------

resource "helm_release" "orbitwatch" {
  name      = "orbitwatch"
  namespace = kubernetes_namespace.app.metadata[0].name
  chart     = local.chart_path

  set {
    name  = "image.registry"
    value = var.image_registry
  }
  set {
    name  = "image.repository"
    value = var.image_repository
  }
  set {
    name  = "image.tag"
    value = var.image_tag
  }
  set {
    name  = "serviceMonitor.enabled"
    value = var.enable_monitoring
  }

  wait    = true
  timeout = 300

  depends_on = [
    kubernetes_resource_quota.app,
    kubernetes_limit_range.app,
    helm_release.monitoring,
    helm_release.signature_policy,
  ]
}

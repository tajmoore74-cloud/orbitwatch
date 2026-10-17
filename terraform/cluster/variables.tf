variable "kubeconfig_path" {
  description = "Path to the kubeconfig for the target cluster"
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Kubeconfig context to use (null = current context)"
  type        = string
  default     = null
}

variable "namespace" {
  description = "Namespace for the OrbitWatch app"
  type        = string
  default     = "orbitwatch"
}

variable "image_registry" {
  description = "Container registry (use your internal registry for air-gapped clusters)"
  type        = string
  default     = "registry.gitlab.com"
}

variable "image_repository" {
  description = "Image repository path"
  type        = string
  default     = "your-gitlab-username/orbitwatch"
}

variable "image_tag" {
  description = "Image tag to deploy (CI sets this to the commit SHA)"
  type        = string
  default     = "0.2.0"
}

variable "enable_monitoring" {
  description = "Install kube-prometheus-stack (Prometheus + Grafana)"
  type        = bool
  default     = true
}

variable "chart_path" {
  description = "Path to the OrbitWatch Helm chart (null = the chart in this repo)"
  type        = string
  default     = null
}

variable "enable_image_signature_policy" {
  description = "Install Kyverno and reject OrbitWatch images not signed with cosign_public_key"
  type        = bool
  default     = false
}

variable "cosign_public_key" {
  description = "Contents of cosign.pub, used to verify image signatures"
  type        = string
  default     = ""

  validation {
    condition     = var.cosign_public_key == "" || can(regex("BEGIN PUBLIC KEY", var.cosign_public_key))
    error_message = "cosign_public_key must be a PEM public key (the contents of cosign.pub)."
  }
}

variable "grafana_admin_password" {
  description = "Grafana admin password. Pass it in with -var or TF_VAR_grafana_admin_password; never commit it."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition     = var.grafana_admin_password == "" || length(var.grafana_admin_password) >= 12
    error_message = "Use at least 12 characters for the Grafana admin password."
  }
}

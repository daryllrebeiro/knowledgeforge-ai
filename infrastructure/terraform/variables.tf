variable "project_id" {
  type        = string
  description = "GCP project hosting KnowledgeForge."
}

variable "region" {
  type    = string
  default = "asia-south1"
}

variable "environment" {
  type    = string
  default = "staging"
}

variable "api_image" {
  type        = string
  description = "Container image for the API service."
}

variable "worker_image" {
  type        = string
  description = "Container image for the ingestion worker."
}

variable "database_password" {
  type        = string
  sensitive   = true
  description = "Password for the KnowledgeForge Cloud SQL user."
}

variable "gemini_api_key" {
  type        = string
  sensitive   = true
  description = "Gemini API key."
}

variable "jwt_secret_key" {
  type        = string
  sensitive   = true
  description = "JWT signing secret (>= 32 characters; validated at boot)."
}

variable "redis_url" {
  type        = string
  default     = ""
  description = "Optional shared Redis URL for cross-replica rate limiting."
}

variable "alert_email" {
  type        = string
  description = "Address that receives monitoring alert notifications."
}

variable "api_max_instances" {
  type    = number
  default = 5
}

variable "worker_max_instances" {
  type    = number
  default = 3
}

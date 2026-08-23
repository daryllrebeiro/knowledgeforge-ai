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
  type      = string
  sensitive = true
}

variable "gemini_api_key" {
  type      = string
  sensitive = true
}

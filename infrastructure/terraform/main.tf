resource "google_storage_bucket" "uploads" {
  name                        = "${var.project_id}-knowledgeforge-${var.environment}"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning { enabled = true }
  lifecycle_rule {
    condition { age = 30 }
    action { type = "Delete" }
  }
}

resource "google_pubsub_topic" "ingestion" {
  name = "knowledgeforge-ingestion-${var.environment}"
}

resource "google_pubsub_subscription" "worker" {
  name  = "knowledgeforge-ingestion-worker-${var.environment}"
  topic = google_pubsub_topic.ingestion.id
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }
}

resource "google_pubsub_topic" "dead_letter" {
  name = "knowledgeforge-ingestion-dead-letter-${var.environment}"
}

resource "google_sql_database_instance" "postgres" {
  name             = "knowledgeforge-${var.environment}"
  database_version = "POSTGRES_17"
  region           = var.region
  settings {
    tier              = "db-custom-2-7680"
    availability_type = "REGIONAL"
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }
    ip_configuration { ipv4_enabled = true }
  }
  deletion_protection = true
}

resource "google_sql_database" "knowledgeforge" {
  name     = "knowledgeforge"
  instance = google_sql_database_instance.postgres.name
}

resource "google_cloud_run_v2_service" "api" {
  name     = "knowledgeforge-api-${var.environment}"
  location = var.region
  template {
    containers {
      image = var.api_image
      env {
        name  = "ASYNC_INGESTION"
        value = "true"
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.uploads.name
      }
      env {
        name  = "PUBSUB_TOPIC"
        value = google_pubsub_topic.ingestion.name
      }
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service" "worker" {
  name     = "knowledgeforge-worker-${var.environment}"
  location = var.region
  template {
    scaling { max_instance_count = 3 }
    containers { image = var.worker_image }
  }
}

resource "google_secret_manager_secret" "gemini" {
  secret_id = "knowledgeforge-gemini-${var.environment}"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "gemini" {
  secret      = google_secret_manager_secret.gemini.id
  secret_data = var.gemini_api_key
}

locals {
  api_service_name            = "knowledgeforge-api-${var.environment}"
  worker_service_name         = "knowledgeforge-worker-${var.environment}"
  extraction_service_name     = "knowledgeforge-extraction-${var.environment}"
  outbox_job_name             = "knowledgeforge-outbox-${var.environment}"
  scheduler_job_name          = "knowledgeforge-outbox-scheduler-${var.environment}"
  ingestion_topic             = "knowledgeforge-ingestion-${var.environment}"
  dlq_topic                   = "knowledgeforge-ingestion-dead-letter-${var.environment}"
  worker_subscription         = "knowledgeforge-ingestion-worker-${var.environment}"
  dlq_subscription            = "knowledgeforge-ingestion-dead-letter-sub-${var.environment}"
  extraction_topic            = "knowledgeforge-extraction-${var.environment}"
  extraction_dlq_topic        = "knowledgeforge-extraction-dead-letter-${var.environment}"
  extraction_subscription     = "knowledgeforge-extraction-worker-${var.environment}"
  extraction_dlq_subscription = "knowledgeforge-extraction-dead-letter-sub-${var.environment}"
  db_user                     = "knowledgeforge"

  required_apis = [
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "pubsub.googleapis.com",
    "sqladmin.googleapis.com",
    "monitoring.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "cloudscheduler.googleapis.com",
  ]
}

# ---------------------------------------------------------------------------
# Project services (R5.5)
# ---------------------------------------------------------------------------

resource "google_project_service" "enabled" {
  for_each                   = toset(local.required_apis)
  service                    = each.value
  disable_on_destroy         = false
  disable_dependent_services = false
}

# ---------------------------------------------------------------------------
# Service accounts (R5.5): dedicated accounts, not the default compute account
# ---------------------------------------------------------------------------

resource "google_service_account" "api" {
  account_id   = "knowledgeforge-api-${var.environment}"
  display_name = "KnowledgeForge API (${var.environment})"
}

resource "google_service_account" "worker" {
  account_id   = "knowledgeforge-worker-${var.environment}"
  display_name = "KnowledgeForge worker (${var.environment})"
}

resource "google_service_account" "pubsub_push" {
  account_id   = "knowledgeforge-pubsub-push-${var.environment}"
  display_name = "Pub/Sub push identity for the worker (${var.environment})"
}

# Phase 2.5: extraction runtime, outbox dispatcher, scheduler, and a second
# push identity — each with its own blast radius and least-privilege bindings.
resource "google_service_account" "extraction" {
  account_id   = "knowledgeforge-extraction-${var.environment}"
  display_name = "KnowledgeForge extraction worker (${var.environment})"
}

resource "google_service_account" "outbox" {
  account_id   = "knowledgeforge-outbox-${var.environment}"
  display_name = "KnowledgeForge outbox dispatcher (${var.environment})"
}

resource "google_service_account" "scheduler" {
  account_id   = "knowledgeforge-scheduler-${var.environment}"
  display_name = "Cloud Scheduler identity for the outbox job (${var.environment})"
}

resource "google_service_account" "extraction_push" {
  account_id   = "knowledgeforge-extraction-push-${var.environment}"
  display_name = "Pub/Sub push identity for the extraction worker (${var.environment})"
}

# ---------------------------------------------------------------------------
# Storage, Pub/Sub (R5.3: push delivery with OIDC)
# ---------------------------------------------------------------------------

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
  name = local.ingestion_topic
}

resource "google_pubsub_topic" "dead_letter" {
  name = local.dlq_topic
}

# Dead-letter sink: a real subscription so DLQ depth is measurable and alertable.
resource "google_pubsub_subscription" "dead_letter" {
  name                       = local.dlq_subscription
  topic                      = google_pubsub_topic.dead_letter.id
  message_retention_duration = "604800s" # 7 days
  retain_acked_messages      = false
  expiration_policy {
    ttl = "" # never expire
  }
}

resource "google_pubsub_subscription" "worker" {
  name  = local.worker_subscription
  topic = google_pubsub_topic.ingestion.id

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.worker.uri}/"

    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
      audience              = google_cloud_run_v2_service.worker.uri
    }
  }

  depends_on = [google_service_account_iam_member.pubsub_push_token_creator]
}

# ---------------------------------------------------------------------------
# Phase 2.5: extraction topic/subscription (separate from ingestion so
# extraction backpressure can never block chunk ingestion), dead-letter
# sink, outbox Cloud Run Job, and the Cloud Scheduler trigger.
# ---------------------------------------------------------------------------

resource "google_pubsub_topic" "extraction" {
  name = local.extraction_topic
}

resource "google_pubsub_topic" "extraction_dead_letter" {
  name = local.extraction_dlq_topic
}

resource "google_pubsub_subscription" "extraction_dead_letter" {
  name                       = local.extraction_dlq_subscription
  topic                      = google_pubsub_topic.extraction_dead_letter.id
  message_retention_duration = "604800s" # 7 days
  retain_acked_messages      = false
  expiration_policy {
    ttl = "" # never expire
  }
}

resource "google_pubsub_subscription" "extraction_worker" {
  name  = local.extraction_subscription
  topic = google_pubsub_topic.extraction.id

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.extraction_dead_letter.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.extraction.uri}/"

    oidc_token {
      service_account_email = google_service_account.extraction_push.email
      audience              = google_cloud_run_v2_service.extraction.uri
    }
  }

  depends_on = [google_service_account_iam_member.extraction_push_token_creator]
}

resource "google_pubsub_topic_iam_member" "extraction_dead_letter_publisher" {
  topic  = google_pubsub_topic.extraction_dead_letter.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.extraction_push.email}"
}

# ---------------------------------------------------------------------------
# Database (R5.4): Cloud SQL user + private connectivity via the Cloud SQL
# instance volume mount (no public-IP database traffic).
# ---------------------------------------------------------------------------

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
    ip_configuration { ipv4_enabled = false }
  }
  deletion_protection = true
}

resource "google_sql_database" "knowledgeforge" {
  name     = "knowledgeforge"
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "app" {
  name     = local.db_user
  instance = google_sql_database_instance.postgres.name
  password = var.database_password
}

# ---------------------------------------------------------------------------
# Secrets (R5.1/R5.2): the database URL embeds the password, so it is stored
# as a Secret Manager version rather than a plaintext env var.
# ---------------------------------------------------------------------------

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

resource "google_secret_manager_secret" "jwt" {
  secret_id = "knowledgeforge-jwt-${var.environment}"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "jwt" {
  secret      = google_secret_manager_secret.jwt.id
  secret_data = var.jwt_secret_key
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "knowledgeforge-database-url-${var.environment}"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret = google_secret_manager_secret.database_url.id
  secret_data = (
    "postgresql://${local.db_user}:${var.database_password}@/knowledgeforge" +
    "?host=/cloudsql/${google_sql_database_instance.postgres.connection_name}"
  )
}

# ---------------------------------------------------------------------------
# IAM (R5.3/R5.4/R5.5)
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret_iam_member" "api_gemini" {
  secret_id = google_secret_manager_secret.gemini.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_jwt" {
  secret_id = google_secret_manager_secret.jwt.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_gemini" {
  secret_id = google_secret_manager_secret.gemini.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

# Both services connect to Cloud SQL through the instance socket.
resource "google_project_iam_member" "api_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "worker_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

# The worker reads and deletes raw objects in the uploads bucket.
resource "google_storage_bucket_iam_member" "worker_object_admin" {
  bucket = google_storage_bucket.uploads.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker.email}"
}

# Pub/Sub push: the push identity may invoke the worker and mint OIDC tokens.
resource "google_cloud_run_v2_service_iam_member" "pubsub_push_invoker" {
  name     = google_cloud_run_v2_service.worker.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_push.email}"
}

resource "google_service_account_iam_member" "pubsub_push_token_creator" {
  service_account_id = google_service_account.pubsub_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.pubsub_push.email}"
}

# The subscription identity publishes to the dead-letter topic on retry
# exhaustion (Pub/Sub dead-letter policy requirement).
resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  topic  = google_pubsub_topic.dead_letter.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.pubsub_push.email}"
}

# The API is the only public service.
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------------------
# Cloud Run services (R5.1/R5.2)
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "api" {
  name     = local.api_service_name
  location = var.region

  template {
    service_account = google_service_account.api.email
    scaling {
      min_instance_count = 0
      max_instance_count = var.api_max_instances
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance { instances = [google_sql_database_instance.postgres.id] }
    }
    containers {
      image = var.api_image
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name  = "ENVIRONMENT"
        value = "production"
      }
      env {
        name  = "ASYNC_INGESTION"
        value = "true"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
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
        name = "PUBSUB_SUBSCRIPTION"
        # local.*, not the subscription resource: the subscription's push
        # endpoint references this service, so a resource reference would be
        # a dependency cycle.
        value = local.worker_subscription
      }
      env {
        name  = "REDIS_URL"
        value = var.redis_url
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "JWT_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt.secret_id
            version = "latest"
          }
        }
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

  depends_on = [
    google_project_service.enabled,
    google_secret_manager_secret_iam_member.api_database_url,
    google_secret_manager_secret_iam_member.api_jwt,
    google_secret_manager_secret_iam_member.api_gemini,
    google_project_iam_member.api_cloudsql_client,
  ]
}

resource "google_cloud_run_v2_service" "worker" {
  name     = local.worker_service_name
  location = var.region

  # No allUsers invoker binding: only the Pub/Sub push identity may call it.

  template {
    service_account = google_service_account.worker.email
    scaling {
      min_instance_count = 0
      max_instance_count = var.worker_max_instances
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance { instances = [google_sql_database_instance.postgres.id] }
    }
    containers {
      image = var.worker_image
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name  = "ENVIRONMENT"
        value = "production"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.uploads.name
      }
      env {
        name = "PUBSUB_SUBSCRIPTION"
        # local.*, not the subscription resource: the subscription's push
        # endpoint references this service, so a resource reference would be
        # a dependency cycle.
        value = local.worker_subscription
      }
      env {
        name  = "LOCAL_EMBEDDINGS"
        value = "false"
      }
      # In-app OIDC verification on top of Cloud Run invoker IAM (R5.3).
      env {
        name  = "WORKER_OIDC_AUDIENCE"
        value = google_cloud_run_v2_service.worker.uri
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
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

  depends_on = [
    google_project_service.enabled,
    google_secret_manager_secret_iam_member.worker_database_url,
    google_secret_manager_secret_iam_member.worker_gemini,
    google_project_iam_member.worker_cloudsql_client,
    google_storage_bucket_iam_member.worker_object_admin,
  ]
}

# ---------------------------------------------------------------------------
# Phase 2.5: extraction worker service (separate blast radius from ingestion)
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "extraction" {
  name     = local.extraction_service_name
  location = var.region

  # No allUsers invoker binding: only the extraction push identity may call it.

  template {
    service_account = google_service_account.extraction.email
    scaling {
      min_instance_count = 0
      max_instance_count = var.worker_max_instances
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance { instances = [google_sql_database_instance.postgres.id] }
    }
    containers {
      image = var.worker_image
      command = ["python", "-m", "uvicorn",
        "knowledgeforge.worker.extraction_entrypoint:app",
      "--host", "0.0.0.0", "--port", "8080"]
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name  = "ENVIRONMENT"
        value = "production"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.uploads.name
      }
      env {
        name  = "EXTRACTION_SUBSCRIPTION"
        value = local.extraction_subscription
      }
      # In-app OIDC verification on top of Cloud Run invoker IAM.
      env {
        name  = "EXTRACTION_WORKER_OIDC_AUDIENCE"
        value = google_cloud_run_v2_service.extraction.uri
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
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

  depends_on = [
    google_project_service.enabled,
    google_secret_manager_secret_iam_member.extraction_database_url,
    google_secret_manager_secret_iam_member.extraction_gemini,
    google_project_iam_member.extraction_cloudsql_client,
    google_storage_bucket_iam_member.extraction_object_admin,
  ]
}

# Bounded outbox dispatcher: one-shot Cloud Run Job (no always-on poller).
resource "google_cloud_run_v2_job" "outbox" {
  name     = local.outbox_job_name
  location = var.region

  template {
    template {
      service_account = google_service_account.outbox.email
      containers {
        image   = var.worker_image
        command = ["python", "-m", "knowledgeforge.extraction.outbox_dispatcher"]
        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
        env {
          name  = "ENVIRONMENT"
          value = "production"
        }
        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "EXTRACTION_TOPIC"
          value = google_pubsub_topic.extraction.name
        }
        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_secret_manager_secret_iam_member.outbox_database_url,
    google_project_iam_member.outbox_cloudsql_client,
  ]
}

# ---------------------------------------------------------------------------
# Phase 2.5 IAM: extraction/outbox/scheduler identities.
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret_iam_member" "extraction_gemini" {
  secret_id = google_secret_manager_secret.gemini.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.extraction.email}"
}

resource "google_secret_manager_secret_iam_member" "extraction_database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.extraction.email}"
}

resource "google_secret_manager_secret_iam_member" "outbox_database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.outbox.email}"
}

resource "google_project_iam_member" "extraction_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.extraction.email}"
}

resource "google_project_iam_member" "outbox_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.outbox.email}"
}

resource "google_storage_bucket_iam_member" "extraction_object_admin" {
  bucket = google_storage_bucket.uploads.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.extraction.email}"
}

# Extraction push: invoke the extraction service and mint OIDC tokens.
resource "google_cloud_run_v2_service_iam_member" "extraction_push_invoker" {
  name     = google_cloud_run_v2_service.extraction.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.extraction_push.email}"
}

resource "google_service_account_iam_member" "extraction_push_token_creator" {
  service_account_id = google_service_account.extraction_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.extraction_push.email}"
}

# The scheduler identity executes the outbox Cloud Run Job.
resource "google_cloud_run_v2_job_iam_member" "scheduler_outbox_invoker" {
  name     = google_cloud_run_v2_job.outbox.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "outbox_dispatch" {
  name      = local.scheduler_job_name
  region    = var.region
  schedule  = "*/2 * * * *"
  time_zone = "UTC"
  paused    = false

  http_target {
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.outbox.name}:run"
    http_method = "POST"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_project_service.enabled]
}

# ---------------------------------------------------------------------------
# Monitoring as code (R5.6)
# ---------------------------------------------------------------------------

resource "google_monitoring_alert_policy" "api_error_rate" {
  display_name = "KnowledgeForge API error rate (${var.environment})"
  combiner     = "OR"

  conditions {
    display_name = "5xx rate above 2%"
    condition_threshold {
      filter          = <<-EOT
        resource.type="cloud_run_revision"
        AND resource.labels.service_name="${local.api_service_name}"
        AND metric.type="run.googleapis.com/request_count"
        AND metric.labels.response_code_class="5xx"
      EOT
      comparison      = "COMPARISON_GT"
      threshold_value = 0.02
      duration        = "300s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_alert_policy" "api_latency" {
  display_name = "KnowledgeForge API P95 latency (${var.environment})"
  combiner     = "OR"

  conditions {
    display_name = "P95 request latency above 5s"
    condition_threshold {
      filter          = <<-EOT
        resource.type="cloud_run_revision"
        AND resource.labels.service_name="${local.api_service_name}"
        AND metric.type="run.googleapis.com/request_latencies"
      EOT
      comparison      = "COMPARISON_GT"
      threshold_value = 5000
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_PERCENTILE_95"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_alert_policy" "dead_letter_depth" {
  display_name = "KnowledgeForge dead-letter queue non-empty (${var.environment})"
  combiner     = "OR"

  conditions {
    display_name = "Dead-letter subscription has undelivered messages"
    condition_threshold {
      # Watch the dead-letter SUBSCRIPTION (not the worker subscription), with
      # an explicit subscription filter.
      filter          = <<-EOT
        resource.type="pubsub_subscription"
        AND resource.labels.subscription_id="${local.dlq_subscription}"
        AND metric.type="pubsub.googleapis.com/subscription/num_undelivered_messages"
      EOT
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_notification_channel" "email" {
  display_name = "KnowledgeForge alerts (${var.environment})"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_slo" "ask_availability" {
  service             = google_cloud_run_v2_service.api.name
  slo_id              = "ask-availability"
  display_name        = "Ask availability (${var.environment})"
  goal                = 0.99
  rolling_period_days = 30

  request_based_sli {
    good_total_ratio {
      good_service_filter  = <<-EOT
        resource.type="cloud_run_revision"
        AND resource.labels.service_name="${local.api_service_name}"
        AND metric.type="run.googleapis.com/request_count"
        AND metric.labels.response_code_class="2xx"
      EOT
      total_service_filter = <<-EOT
        resource.type="cloud_run_revision"
        AND resource.labels.service_name="${local.api_service_name}"
        AND metric.type="run.googleapis.com/request_count"
        AND metric.labels.response_code_class!="4xx"
      EOT
    }
  }
}

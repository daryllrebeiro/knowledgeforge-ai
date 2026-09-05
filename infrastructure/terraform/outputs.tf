output "api_url" {
  description = "Public URL of the API Cloud Run service."
  value       = google_cloud_run_v2_service.api.uri
}

output "worker_url" {
  description = "URL of the worker service (private; Pub/Sub push target)."
  value       = google_cloud_run_v2_service.worker.uri
}

output "database_instance" {
  description = "Cloud SQL connection name (for psql / admin tooling)."
  value       = google_sql_database_instance.postgres.connection_name
}

output "uploads_bucket" {
  description = "GCS bucket that receives raw document uploads."
  value       = google_storage_bucket.uploads.name
}

output "worker_subscription" {
  description = "Pub/Sub push subscription feeding the worker."
  value       = google_pubsub_subscription.worker.name
}

output "dead_letter_subscription" {
  description = "Subscription backing the dead-letter topic (alertable depth)."
  value       = google_pubsub_subscription.dead_letter.name
}

output "extraction_url" {
  description = "URL of the extraction worker service (private; Pub/Sub push target)."
  value       = google_cloud_run_v2_service.extraction.uri
}

output "extraction_subscription" {
  description = "Pub/Sub push subscription feeding the extraction worker."
  value       = google_pubsub_subscription.extraction_worker.name
}

output "outbox_job" {
  description = "Cloud Run Job that dispatches extraction outbox events."
  value       = google_cloud_run_v2_job.outbox.name
}

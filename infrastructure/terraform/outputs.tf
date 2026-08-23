output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "database_instance" {
  value = google_sql_database_instance.postgres.connection_name
}

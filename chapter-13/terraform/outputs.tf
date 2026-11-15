output "atlas_srv_connection_string" {
  description = "Atlas SRV connection string for the training cluster; add credentials using a secret manager."
  value       = mongodbatlas_cluster.training.connection_strings[0].standard_srv
}

output "atlas_project_id" {
  description = "Atlas project identifier created for Chapter 13."
  value       = mongodbatlas_project.chapter13.id
}

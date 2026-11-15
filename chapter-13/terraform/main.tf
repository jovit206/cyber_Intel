resource "mongodbatlas_project" "chapter13" {
  name   = "NoSQL Chapter 13 Training"
  org_id = var.atlas_org_id
}

resource "mongodbatlas_cluster" "training" {
  project_id   = mongodbatlas_project.chapter13.id
  name         = "chapter13-training"
  cluster_type = "REPLICASET"

  provider_name               = "AWS"
  provider_region_name        = var.atlas_region
  provider_instance_size_name = "M0"
  mongo_db_major_version      = "7.0"
  cloud_backup                = false
}

resource "mongodbatlas_database_user" "lab_user" {
  project_id         = mongodbatlas_project.chapter13.id
  username           = "lab_user"
  password           = var.atlas_user_password
  auth_database_name = "admin"

  roles {
    role_name     = "readWrite"
    database_name = "lab13"
  }
}

resource "mongodbatlas_ip_access_list" "client" {
  project_id = mongodbatlas_project.chapter13.id
  cidr_block = var.atlas_client_cidr
  comment    = "Chapter 13 training client; restrict to the operator's current network."
}

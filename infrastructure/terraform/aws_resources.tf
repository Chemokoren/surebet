resource "aws_ecr_repository" "futurapredict" {
  name                 = "${var.app_name}-${var.environment}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Environment = var.environment
    Application = var.app_name
  }
}

resource "aws_ecs_cluster" "futurapredict" {
  name = "${var.app_name}-${var.environment}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Environment = var.environment
    Application = var.app_name
  }
}

resource "aws_rds_cluster" "futurapredict" {
  cluster_identifier      = "${var.app_name}-${var.environment}-db"
  engine                  = "aurora-postgresql"
  engine_version          = "15.2"
  database_name           = "futurapredict"
  master_username         = "admin"
  master_password         = random_password.db_password.result
  backup_retention_period = 7
  preferred_backup_window = "03:00-04:00"
  skip_final_snapshot     = false
  final_snapshot_identifier = "${var.app_name}-${var.environment}-final-snapshot"

  tags = {
    Environment = var.environment
    Application = var.app_name
  }
}

resource "random_password" "db_password" {
  length  = 32
  special = true
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${var.app_name}-${var.environment}-redis"
  engine               = "redis"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.0"
  port                 = 6379

  tags = {
    Environment = var.environment
    Application = var.app_name
  }
}

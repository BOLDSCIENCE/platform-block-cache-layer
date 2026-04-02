variable "environment" {
  type        = string
  description = "Deployment environment (dev, staging, production)"
  default     = "dev"
}

variable "aws_region" {
  type        = string
  description = "AWS region"
  default     = "us-east-1"
}

variable "api_lambda_zip_path" {
  type        = string
  description = "Path to the API Lambda deployment zip"
  default     = "../api/build/cache-layer-api.zip"
}

variable "api_lambda_memory_size" {
  type        = number
  description = "Memory size for the API Lambda (MB)"
  default     = 512
}

variable "api_lambda_timeout" {
  type        = number
  description = "Timeout for the API Lambda (seconds)"
  default     = 30
}

variable "log_level" {
  type        = string
  description = "Application log level"
  default     = "INFO"
}

variable "log_retention_days" {
  type        = number
  description = "CloudWatch log group retention in days"
  default     = 30
}

variable "allowed_origins" {
  type        = list(string)
  description = "Allowed CORS origins"
  default     = ["http://localhost:5173"]
}

variable "custom_domain" {
  type        = string
  description = "Custom domain for the API Gateway (e.g., cache-api.dev.boldquantum.com)"
  default     = ""
}

# Event handler Lambda variables
variable "event_handler_lambda_memory_size" {
  type        = number
  description = "Memory size for the Event Handler Lambda (MB)"
  default     = 256
}

variable "event_handler_lambda_timeout" {
  type        = number
  description = "Timeout for the Event Handler Lambda (seconds)"
  default     = 60
}

# OpenSearch variables
variable "opensearch_instance_type" {
  type        = string
  description = "OpenSearch instance type"
  default     = "t3.small.search"
}

variable "opensearch_instance_count" {
  type        = number
  description = "Number of OpenSearch data nodes"
  default     = 1
}

variable "opensearch_ebs_volume_size" {
  type        = number
  description = "EBS volume size in GB for OpenSearch nodes"
  default     = 20
}

# Stats aggregator Lambda variables
variable "stats_aggregator_lambda_memory_size" {
  type        = number
  description = "Memory size for the Stats Aggregator Lambda (MB)"
  default     = 256
}

variable "stats_aggregator_lambda_timeout" {
  type        = number
  description = "Timeout for the Stats Aggregator Lambda (seconds)"
  default     = 120
}

variable "application_id" {
  type        = string
  description = "Application ID for the cache layer tenant"
  default     = ""
}

variable "client_id" {
  type        = string
  description = "Client ID for the cache layer tenant"
  default     = ""
}

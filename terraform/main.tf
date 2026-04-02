terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "bold-terraform-state"
    key            = "cache-layer/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "bold-terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

# -----------------------------------------------------------------------------
# Data sources
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_ssm_parameter" "bold_auth_authorizer_arn" {
  name = "/bold/auth/authorizer-arn"
}

data "aws_ssm_parameter" "bold_auth_authorizer_invoke_role_arn" {
  name = "/bold/auth/authorizer-invoke-role-arn"
}

data "aws_ssm_parameter" "dns_hosted_zone_id" {
  count = var.custom_domain != "" ? 1 : 0
  name  = "/bold/dns/${var.environment}/hosted-zone-id"
}

data "aws_ssm_parameter" "dns_wildcard_cert_arn" {
  count = var.custom_domain != "" ? 1 : 0
  name  = "/bold/dns/${var.environment}/wildcard-cert-arn"
}

data "aws_ssm_parameter" "model_gateway_api_url" {
  name = "/bold/model-gateway/api-url"
}


# -----------------------------------------------------------------------------
# Locals
# -----------------------------------------------------------------------------

locals {
  name_prefix = "cache-layer-${var.environment}"
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.name

  common_tags = {
    Service     = "cache-layer"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# -----------------------------------------------------------------------------
# OpenSearch domain — provisioned for Phase 2 semantic caching
# The Cache Layer provisions the shared OpenSearch domain. The endpoint is
# registered in SSM for other blocks to discover.
# -----------------------------------------------------------------------------

resource "aws_opensearch_domain" "main" {
  domain_name    = "bold-opensearch-${var.environment}"
  engine_version = "OpenSearch_2.11"

  cluster_config {
    instance_type  = var.opensearch_instance_type
    instance_count = var.opensearch_instance_count
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = var.opensearch_ebs_volume_size
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = aws_iam_role.api_lambda.arn }
        Action    = "es:*"
        Resource  = "arn:aws:es:${local.region}:${local.account_id}:domain/bold-opensearch-${var.environment}/*"
      }
    ]
  })

  tags = {
    Name = "bold-opensearch-${var.environment}"
  }
}

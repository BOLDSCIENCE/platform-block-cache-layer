# -----------------------------------------------------------------------------
# SSM Parameters
# -----------------------------------------------------------------------------

resource "aws_ssm_parameter" "api_url" {
  name        = "/bold/cache-layer/api-url"
  type        = "String"
  value       = var.custom_domain != "" ? "https://${var.custom_domain}" : aws_apigatewayv2_stage.default.invoke_url
  description = "Cache Layer API endpoint URL"

  tags = {
    Name = "cache-layer-api-url"
  }
}

resource "aws_ssm_parameter" "opensearch_endpoint" {
  name        = "/bold/opensearch/domain-endpoint"
  type        = "String"
  value       = aws_opensearch_domain.main.endpoint
  description = "Shared OpenSearch domain endpoint (provisioned by Cache Layer)"

  tags = {
    Name = "opensearch-domain-endpoint"
  }
}

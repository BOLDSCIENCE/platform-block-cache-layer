# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "api_url" {
  description = "API Gateway endpoint URL"
  value       = var.custom_domain != "" ? "https://${var.custom_domain}" : aws_apigatewayv2_stage.default.invoke_url
}

output "api_function_arn" {
  description = "API Lambda function ARN"
  value       = aws_lambda_function.api.arn
}

output "table_name" {
  description = "DynamoDB table name"
  value       = aws_dynamodb_table.main.name
}

output "opensearch_endpoint" {
  description = "OpenSearch domain endpoint"
  value       = aws_opensearch_domain.main.endpoint
}

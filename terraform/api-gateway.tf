# -----------------------------------------------------------------------------
# HTTP API
# -----------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "main" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.allowed_origins
    allow_methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
    allow_headers = [
      "Content-Type",
      "Authorization",
      "X-API-Key",
      "X-Forwarded-Client-Id",
    ]
    max_age = 3600
  }

  tags = {
    Name = "${local.name_prefix}-api"
  }
}

# -----------------------------------------------------------------------------
# CloudWatch log group for API Gateway access logs
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${local.name_prefix}-api"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name_prefix}-api-gateway-logs"
  }
}

# -----------------------------------------------------------------------------
# Default stage with access logging
# -----------------------------------------------------------------------------

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
    })
  }

  tags = {
    Name = "${local.name_prefix}-default-stage"
  }
}

# -----------------------------------------------------------------------------
# Lambda integration
# -----------------------------------------------------------------------------

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# -----------------------------------------------------------------------------
# Authorizer — Bold Auth (shared Lambda Authorizer)
# -----------------------------------------------------------------------------

resource "aws_apigatewayv2_authorizer" "bold" {
  api_id           = aws_apigatewayv2_api.main.id
  authorizer_type  = "REQUEST"
  name             = "BoldAuthorizer"
  identity_sources = ["$request.header.x-api-key"]

  authorizer_uri                    = "arn:aws:apigateway:${local.region}:lambda:path/2015-03-31/functions/${data.aws_ssm_parameter.bold_auth_authorizer_arn.value}/invocations"
  authorizer_credentials_arn        = data.aws_ssm_parameter.bold_auth_authorizer_invoke_role_arn.value
  authorizer_payload_format_version = "2.0"
  enable_simple_responses           = true
}

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

# Health — no auth
resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"

  authorization_type = "NONE"
}

# All other routes — custom authorizer
resource "aws_apigatewayv2_route" "proxy" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"

  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.bold.id
}

# OPTIONS — no auth (CORS preflight)
resource "aws_apigatewayv2_route" "options" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "OPTIONS /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"

  authorization_type = "NONE"
}

# -----------------------------------------------------------------------------
# Lambda permissions for API Gateway
# -----------------------------------------------------------------------------

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

# -----------------------------------------------------------------------------
# Custom domain (optional)
# -----------------------------------------------------------------------------

resource "aws_apigatewayv2_domain_name" "api" {
  count       = var.custom_domain != "" ? 1 : 0
  domain_name = var.custom_domain

  domain_name_configuration {
    certificate_arn = data.aws_ssm_parameter.dns_wildcard_cert_arn[0].value
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }

  tags = {
    Name = var.custom_domain
  }
}

resource "aws_apigatewayv2_api_mapping" "api" {
  count       = var.custom_domain != "" ? 1 : 0
  api_id      = aws_apigatewayv2_api.main.id
  domain_name = aws_apigatewayv2_domain_name.api[0].id
  stage       = aws_apigatewayv2_stage.default.id
}

resource "aws_route53_record" "api" {
  count   = var.custom_domain != "" ? 1 : 0
  zone_id = data.aws_ssm_parameter.dns_hosted_zone_id[0].value
  name    = var.custom_domain
  type    = "A"

  alias {
    name                   = aws_apigatewayv2_domain_name.api[0].domain_name_configuration[0].target_domain_name
    zone_id                = aws_apigatewayv2_domain_name.api[0].domain_name_configuration[0].hosted_zone_id
    evaluate_target_health = false
  }
}

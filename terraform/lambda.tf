# -----------------------------------------------------------------------------
# CloudWatch Log Group
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name_prefix}-api"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name_prefix}-api-logs"
  }
}

# -----------------------------------------------------------------------------
# API Lambda function
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "api" {
  function_name = "${local.name_prefix}-api"
  role          = aws_iam_role.api_lambda.arn
  handler       = "src.main.handler"
  runtime       = "python3.12"
  architectures = ["arm64"]
  memory_size   = var.api_lambda_memory_size
  timeout       = var.api_lambda_timeout

  filename         = var.api_lambda_zip_path
  source_code_hash = filebase64sha256(var.api_lambda_zip_path)

  layers = [
    "arn:aws:lambda:${local.region}:901920570463:layer:aws-otel-python-arm64-ver-1-25-0:1",
  ]

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      DYNAMODB_TABLE  = aws_dynamodb_table.main.name
      AWS_REGION_NAME = local.region
      ENVIRONMENT     = var.environment
      LOG_LEVEL       = var.log_level
      ALLOWED_ORIGINS = jsonencode(var.allowed_origins)
    }
  }

  depends_on = [aws_cloudwatch_log_group.api]

  tags = {
    Name = "${local.name_prefix}-api"
  }
}

# -----------------------------------------------------------------------------
# IAM role for API Lambda
# -----------------------------------------------------------------------------

resource "aws_iam_role" "api_lambda" {
  name = "${local.name_prefix}-api-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  inline_policy {
    name = "api-lambda-policy"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid    = "CloudWatchLogs"
          Effect = "Allow"
          Action = [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
          ]
          Resource = "${aws_cloudwatch_log_group.api.arn}:*"
        },
        {
          Sid    = "DynamoDB"
          Effect = "Allow"
          Action = [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
            "dynamodb:DeleteItem",
            "dynamodb:Query",
            "dynamodb:Scan",
            "dynamodb:BatchGetItem",
            "dynamodb:BatchWriteItem",
          ]
          Resource = [
            aws_dynamodb_table.main.arn,
            "${aws_dynamodb_table.main.arn}/index/*",
          ]
        },
        {
          Sid    = "XRay"
          Effect = "Allow"
          Action = [
            "xray:PutTraceSegments",
            "xray:PutTelemetryRecords",
            "xray:GetSamplingRules",
            "xray:GetSamplingTargets",
          ]
          Resource = "*"
        },
      ]
    })
  }

  tags = {
    Name = "${local.name_prefix}-api-lambda"
  }
}

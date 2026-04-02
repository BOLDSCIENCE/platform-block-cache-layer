# -----------------------------------------------------------------------------
# EventBridge rule for cache invalidation events
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "cache_invalidation" {
  name        = "${local.name_prefix}-invalidation"
  description = "Route Doc Ingest and Model Gateway events to cache invalidation handler"

  event_pattern = jsonencode({
    source      = ["bold.doc-ingest", "bold.model-gateway"]
    detail-type = ["DocumentIngested", "ModelVersionChanged"]
  })

  tags = {
    Name = "${local.name_prefix}-invalidation-rule"
  }
}

resource "aws_cloudwatch_event_target" "event_handler" {
  rule = aws_cloudwatch_event_rule.cache_invalidation.name
  arn  = aws_lambda_function.event_handler.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  function_name = aws_lambda_function.event_handler.function_name
  action        = "lambda:InvokeFunction"
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cache_invalidation.arn
}

# -----------------------------------------------------------------------------
# Scheduled rule for stats aggregation (every 15 minutes)
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "stats_aggregator" {
  name                = "${local.name_prefix}-stats-aggregator"
  description         = "Trigger stats aggregation every 15 minutes"
  schedule_expression = "rate(15 minutes)"

  tags = {
    Name = "${local.name_prefix}-stats-aggregator-rule"
  }
}

resource "aws_cloudwatch_event_target" "stats_aggregator" {
  rule = aws_cloudwatch_event_rule.stats_aggregator.name
  arn  = aws_lambda_function.stats_aggregator.arn
}

resource "aws_lambda_permission" "stats_aggregator_eventbridge" {
  statement_id  = "AllowEventBridgeStatsAggregator"
  function_name = aws_lambda_function.stats_aggregator.function_name
  action        = "lambda:InvokeFunction"
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.stats_aggregator.arn
}

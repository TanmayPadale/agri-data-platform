output "raw_bucket" {
  description = "Where the Lambda writes weather/dt=YYYY-MM-DD/... objects."
  value       = module.raw_bucket.name
}

output "lambda_function" {
  value = aws_lambda_function.weather_to_s3.function_name
}

output "schedule_rule_arn" {
  value = aws_cloudwatch_event_rule.daily_weather.arn
}

# A daily Lambda that copies Open-Meteo weather into the raw bucket, scheduled by
# EventBridge. Every permission is scoped as narrowly as the job allows.

# ---------------------------------------------------------------- package

data "archive_file" "weather_to_s3" {
  type        = "zip"
  source_file = "${path.module}/lambda/weather_to_s3/handler.py"
  output_path = "${path.module}/.build/weather_to_s3.zip"
}

# ---------------------------------------------------------------- logs
# Created by Terraform, before the function, so it has a retention period. If the
# function created it on first run, logs would be kept (and billed) forever.

resource "aws_cloudwatch_log_group" "weather_to_s3" {
  name              = "/aws/lambda/agri-weather-to-s3"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------- IAM: least privilege

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "weather_to_s3" {
  name               = "agri-weather-to-s3"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "weather_to_s3" {
  statement {
    sid       = "WriteWeatherPrefixOnly"
    actions   = ["s3:PutObject"]
    resources = ["${module.raw_bucket.arn}/weather/*"]
  }

  statement {
    sid       = "OwnLogGroupOnly"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.weather_to_s3.arn}:*"]
  }
}

resource "aws_iam_role_policy" "weather_to_s3" {
  name   = "write-weather-and-logs"
  role   = aws_iam_role.weather_to_s3.id
  policy = data.aws_iam_policy_document.weather_to_s3.json
}

# ---------------------------------------------------------------- function

resource "aws_lambda_function" "weather_to_s3" {
  function_name = "agri-weather-to-s3"
  role          = aws_iam_role.weather_to_s3.arn
  runtime       = "python3.13"
  handler       = "handler.handler"
  filename      = data.archive_file.weather_to_s3.output_path
  # A new hash on every code change is what makes `terraform apply` redeploy it.
  source_code_hash = data.archive_file.weather_to_s3.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      BUCKET         = module.raw_bucket.name
      LOCATIONS_JSON = jsonencode(local.locations)
    }
  }

  depends_on = [aws_cloudwatch_log_group.weather_to_s3, aws_iam_role_policy.weather_to_s3]
}

# ---------------------------------------------------------------- schedule

resource "aws_cloudwatch_event_rule" "daily_weather" {
  name                = "agri-daily-weather"
  description         = "Copy yesterday's farm weather to S3 every morning (Sydney time)."
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "daily_weather" {
  rule = aws_cloudwatch_event_rule.daily_weather.name
  arn  = aws_lambda_function.weather_to_s3.arn
}

# Only this one rule may invoke the function: source_arn pins it.
resource "aws_lambda_permission" "allow_daily_rule" {
  statement_id  = "AllowDailyWeatherRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.weather_to_s3.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_weather.arn
}

# The raw landing bucket: the cloud copy of the weather path. The Lambda writes daily
# JSON under weather/dt=YYYY-MM-DD/. Nothing reads it yet; it is where a Snowflake
# external stage or an Iceberg table would point next.

module "raw_bucket" {
  source = "./modules/s3_bucket"

  name                  = "agri-raw-${var.suffix}"
  transition_to_ia_days = 30
  force_destroy         = var.force_destroy
}

locals {
  # The same farms file the Python ingest and dbt use, decoded at plan time.
  locations = csvdecode(file("${path.module}/../seeds/locations.csv"))
}

# Values for the free local emulator (make tf-emulator-apply). Real AWS: pass your
# own -var suffix=... and leave aws_endpoint and force_destroy unset.
suffix        = "local"
aws_endpoint  = "http://localhost:4566"
force_destroy = true

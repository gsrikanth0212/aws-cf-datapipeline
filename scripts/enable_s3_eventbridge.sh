#!/usr/bin/env bash
# ONE-TIME setup: make the raw bucket send "Object Created" events to EventBridge,
# so the EventBridge rule in cf_cloudformation/stepfunctions.yaml can start the pipeline.
#
# The raw bucket already exists and is not managed by CloudFormation, which is why this
# is a script and not part of a template.
#
# WARNING: put-bucket-notification-configuration REPLACES the bucket's notification config.
# The script checks for an existing config first and stops if there is one.
#
# Usage: ./scripts/enable_s3_eventbridge.sh [bucket] [region]
set -euo pipefail

BUCKET="${1:-demo-learn-cloudform}"
REGION="${2:-us-east-1}"

CURRENT=$(aws s3api get-bucket-notification-configuration --bucket "$BUCKET" --region "$REGION" --output json)
if echo "$CURRENT" | grep -q "EventBridgeConfiguration"; then
  echo "EventBridge notifications are already enabled on $BUCKET"
  exit 0
fi
if [ -n "$CURRENT" ] && [ "$CURRENT" != "{}" ] && [ "$CURRENT" != "null" ]; then
  echo "Bucket $BUCKET already has notifications configured:"
  echo "$CURRENT"
  echo "Merge {\"EventBridgeConfiguration\": {}} into it manually to avoid losing them."
  exit 1
fi

aws s3api put-bucket-notification-configuration \
  --bucket "$BUCKET" --region "$REGION" \
  --notification-configuration '{"EventBridgeConfiguration": {}}'
echo "EventBridge notifications enabled on $BUCKET"

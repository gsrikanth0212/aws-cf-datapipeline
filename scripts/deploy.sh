#!/usr/bin/env bash
# Deploy the pipeline to one environment.
#   1. upload artifacts listed in manifest.yaml (Glue scripts, job configs, catalog schemas)
#      and the zipped Lambda code
#   2. `aws cloudformation package` - uploads the nested templates
#   3. `aws cloudformation deploy`  - creates/updates the stack with configs/<env>.yaml values
#
# Usage: ./scripts/deploy.sh <dev|stage|prod>
set -euo pipefail

ENV="${1:?Usage: ./scripts/deploy.sh <dev|stage|prod>}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
CONFIG="configs/${ENV}.yaml"
MANIFEST="manifest.yaml"
[ -f "$CONFIG" ] || { echo "Config $CONFIG not found"; exit 1; }

STACK_NAME=$(yq -r '.stack_name' "$CONFIG")
REGION=$(yq -r '.aws_region' "$CONFIG")
ARTIFACT_BUCKET=$(yq -r '.parameters.ArtifactBucketName' "$CONFIG")
ARTIFACT_PREFIX=$(yq -r '.parameters.ArtifactPrefix' "$CONFIG")
TEMPLATE=$(yq -r '.template' "$MANIFEST")
BUILD_DIR="build"
mkdir -p "$BUILD_DIR"

echo "Deploying '$STACK_NAME' to $ENV ($REGION)"
aws sts get-caller-identity --query Account --output text | sed 's/^/AWS account: /'

echo
echo "==> 1. Upload artifacts to s3://$ARTIFACT_BUCKET/$ARTIFACT_PREFIX/"
for i in $(seq 0 $(( $(yq '.artifacts | length' "$MANIFEST") - 1 ))); do
  src=$(yq -r ".artifacts[$i].source" "$MANIFEST")
  dest=$(yq -r ".artifacts[$i].dest" "$MANIFEST")
  aws s3 sync "$src" "s3://$ARTIFACT_BUCKET/$ARTIFACT_PREFIX/$dest" \
    --delete --exclude "*__pycache__*" --region "$REGION"
done

# Lambda zip: the key contains a hash of the code, so the key changes only when the code does
# (CloudFormation updates the function only when the S3 key changes).
hash_cmd() { if command -v sha256sum >/dev/null; then sha256sum; else shasum -a 256; fi; }
CODE_HASH=$(find lambda/validate_file -type f -name "*.py" | sort | xargs cat | hash_cmd | cut -c1-12)
LAMBDA_KEY="$ARTIFACT_PREFIX/lambda/validate_file-${CODE_HASH}.zip"
rm -f "$BUILD_DIR/validate_file.zip"
(cd lambda/validate_file && zip -qr "$ROOT_DIR/$BUILD_DIR/validate_file.zip" . -x "*__pycache__*" "*.zip")
aws s3 cp "$BUILD_DIR/validate_file.zip" "s3://$ARTIFACT_BUCKET/$LAMBDA_KEY" --region "$REGION"

echo
echo "==> 2. Package templates"
aws cloudformation package \
  --template-file "$TEMPLATE" \
  --s3-bucket "$ARTIFACT_BUCKET" \
  --s3-prefix "$ARTIFACT_PREFIX/cfn" \
  --output-template-file "$BUILD_DIR/packaged-${ENV}.yaml" \
  --region "$REGION"

echo
echo "==> 3. Deploy stack"
# Build "Key=Value" lists from the config file (bash 3 compatible - works on macOS too)
PARAMS=()
while IFS= read -r line; do PARAMS+=("$line"); done \
  < <(yq -r '.parameters | to_entries | .[] | .key + "=" + .value' "$CONFIG")
PARAMS+=("LambdaCodeS3Key=$LAMBDA_KEY")
PARAMS+=("AllowedDatasets=$(yq -r '[.datasets[].name] | join(",")' "$MANIFEST")")

TAGS=()
while IFS= read -r line; do TAGS+=("$line"); done \
  < <(yq -r '.tags | to_entries | .[] | .key + "=" + .value' "$CONFIG")

aws cloudformation deploy \
  --template-file "$BUILD_DIR/packaged-${ENV}.yaml" \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides "${PARAMS[@]}" \
  --tags "${TAGS[@]}" \
  --no-fail-on-empty-changeset

echo
echo "==> Stack outputs"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs" --output table

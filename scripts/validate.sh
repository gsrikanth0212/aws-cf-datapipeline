#!/usr/bin/env bash
# Validate everything BEFORE deploying. Runs locally (VS Code terminal), in GitHub Actions
# and in CodeBuild. Needs: yq, python3, cfn-lint, yamllint, pytest (pip install -r requirements-dev.txt)
# AWS credentials are optional: if present, templates are also checked by the CloudFormation API.
#
# Usage: ./scripts/validate.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
MANIFEST="manifest.yaml"
ERRORS=0

step() { echo; echo "==> $*"; }
fail() { echo "  ERROR: $*"; ERRORS=$((ERRORS + 1)); }

for tool in yq python3 cfn-lint; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool (see README 'Local setup')"; exit 1; }
done

step "1. YAML syntax (yamllint)"
if command -v yamllint >/dev/null; then
  yamllint -c .yamllint configs job_configs catalog manifest.yaml .github buildspecs || fail "yamllint"
else
  echo "  yamllint not installed - skipping"
fi

step "2. CloudFormation lint (cfn-lint)"
# W3002 = "local path only works with aws cloudformation package" - that is exactly how we deploy
cfn-lint --ignore-checks W3002 -- cf_cloudformation/*.yaml cf_cloudformation/bootstrap/*.yaml || fail "cfn-lint"

step "3. Environment configs"
REQUIRED_PARAMS=$(yq -r '.parameters | keys | .[]' configs/dev.yaml)
for env in $(yq -r '.environments[]' "$MANIFEST"); do
  cfg="configs/${env}.yaml"
  [ -f "$cfg" ] || { fail "$cfg not found"; continue; }
  [ "$(yq -r '.environment' "$cfg")" = "$env" ] || fail "$cfg: 'environment' must be '$env'"
  [ "$(yq -r '.parameters.Environment' "$cfg")" = "$env" ] || fail "$cfg: parameters.Environment must be '$env'"
  for p in $REQUIRED_PARAMS; do
    [ "$(yq -r ".parameters.$p" "$cfg")" != "null" ] || fail "$cfg: missing parameters.$p"
  done
  echo "  $cfg OK"
done

step "4. Manifest: every dataset has a job config and a catalog schema"
for i in $(seq 0 $(( $(yq '.datasets | length' "$MANIFEST") - 1 ))); do
  name=$(yq -r ".datasets[$i].name" "$MANIFEST")
  job_config=$(yq -r ".datasets[$i].job_config" "$MANIFEST")
  schema=$(yq -r ".datasets[$i].schema" "$MANIFEST")
  [ -f "$job_config" ] || fail "dataset $name: $job_config not found"
  [ -f "$schema" ] || fail "dataset $name: $schema not found"
  [ "$(yq -r '.dataset' "$job_config")" = "$name" ] || fail "$job_config: 'dataset' must be '$name'"
  [ "$(yq -r '.columns | length' "$schema")" -gt 0 ] || fail "$schema: no columns defined"
  echo "  $name OK"
done
for src in $(yq -r '.artifacts[].source' "$MANIFEST"); do
  [ -d "$src" ] || fail "artifact folder $src not found"
done

step "5. Python syntax + unit tests"
python3 -m py_compile lambda/validate_file/app.py glue_jobs/raw_to_curated.py || fail "python syntax"
if command -v pytest >/dev/null; then
  pytest -q tests --junitxml=build/test-reports/junit.xml || fail "unit tests"
else
  echo "  pytest not installed - skipping tests"
fi

step "6. CloudFormation API validation (only if AWS credentials are available)"
if aws sts get-caller-identity >/dev/null 2>&1; then
  for t in cf_cloudformation/*.yaml; do
    # main.yaml references local nested templates that only `package` can resolve
    case "$t" in *main.yaml) continue ;; esac
    aws cloudformation validate-template --template-body "file://$t" >/dev/null && echo "  $t OK" || fail "$t"
  done
else
  echo "  No AWS credentials - skipping"
fi

echo
if [ "$ERRORS" -gt 0 ]; then
  echo "Validation FAILED with $ERRORS error(s)"
  exit 1
fi
echo "All validations passed"

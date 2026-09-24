"""
Validate a raw file before the Glue job runs.

Called by Step Functions with:
    {"bucket": "demo-learn-cloudform", "key": "raw/customers/customers_20260924.csv", "size": 1234}

Expected key layout:  <RAW_PREFIX><dataset>/<file>.<ext>
    e.g. raw/customers/customers_20260924.csv  -> dataset = "customers"

Always returns the same shape (Step Functions' ResultSelector needs every field):
    {"is_valid": bool, "dataset": str, "s3_path": str, "reason": str}
"""
import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _config():
    return {
        "raw_prefix": os.environ.get("RAW_PREFIX", "raw/"),
        "allowed_datasets": [d.strip() for d in os.environ.get("ALLOWED_DATASETS", "").split(",") if d.strip()],
        "allowed_extensions": [e.strip().lower() for e in os.environ.get("ALLOWED_EXTENSIONS", ".csv").split(",") if e.strip()],
        "max_size_bytes": int(os.environ.get("MAX_FILE_SIZE_MB", "100")) * 1024 * 1024,
    }


def _get_size(bucket, key):
    import boto3  # imported lazily so unit tests don't need AWS

    return boto3.client("s3").head_object(Bucket=bucket, Key=key)["ContentLength"]


def _result(is_valid, dataset, s3_path, reason):
    result = {"is_valid": is_valid, "dataset": dataset, "s3_path": s3_path, "reason": reason}
    logger.info(json.dumps(result))
    return result


def lambda_handler(event, context):
    cfg = _config()
    bucket = event["bucket"]
    key = event["key"]
    s3_path = f"s3://{bucket}/{key}"
    print(f"Validating {s3_path} with config: {cfg}")
    
    if key.endswith("/"):
        return _result(False, "", s3_path, "Folder marker, not a file")

    if not key.startswith(cfg["raw_prefix"]):
        return _result(False, "", s3_path, f"Key is outside the raw prefix '{cfg['raw_prefix']}'")

    parts = key[len(cfg["raw_prefix"]):].split("/")
    if len(parts) < 2:
        return _result(False, "", s3_path, "File must be inside a dataset folder, e.g. raw/customers/file.csv")

    dataset = parts[0]
    if dataset not in cfg["allowed_datasets"]:
        return _result(False, dataset, s3_path, f"Unknown dataset '{dataset}'. Allowed: {cfg['allowed_datasets']}")

    extension = os.path.splitext(key)[1].lower()
    if extension not in cfg["allowed_extensions"]:
        return _result(False, dataset, s3_path, f"Extension '{extension}' not allowed. Allowed: {cfg['allowed_extensions']}")

    size = event.get("size")
    if size is None:
        size = _get_size(bucket, key)
    if size == 0:
        return _result(False, dataset, s3_path, "File is empty")
    if size > cfg["max_size_bytes"]:
        return _result(False, dataset, s3_path, f"File is {size} bytes, larger than the {cfg['max_size_bytes']} byte limit")

    return _result(True, dataset, s3_path, "OK")

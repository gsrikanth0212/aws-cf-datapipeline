"""
Generic, config-driven Glue job: RAW (csv) -> CURATED (parquet).

The same script handles every dataset. What it does for a dataset is described by two
YAML files uploaded to S3 by scripts/deploy.sh:
    <config_s3_prefix>/job_configs/<dataset>.yaml   -> HOW to process (reader options, rules, target)
    <config_s3_prefix>/catalog/<dataset>.yaml       -> WHAT the data looks like (columns, types)

Arguments (set in cf_cloudformation/glue.yaml, --dataset/--input_path overridden by Step Functions):
    --env  --dataset  --input_path  --config_s3_prefix  --curated_bucket
"""
import sys
from urllib.parse import urlparse

import boto3
import yaml  # PyYAML ships with Glue 4.0. If missing, add "--additional-python-modules": "PyYAML"
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F

args = getResolvedOptions(
    sys.argv, ["JOB_NAME", "env", "dataset", "input_path", "config_s3_prefix", "curated_bucket"]
)

sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session
logger = glue_context.get_logger()
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

s3 = boto3.client("s3")


def read_yaml_from_s3(uri):
    parsed = urlparse(uri)
    body = s3.get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))["Body"].read()
    return yaml.safe_load(body)


def deep_merge(base, override):
    """Merge environment_overrides on top of the base job config."""
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# ------------------------------------------------------------------ 1. Load configs
env = args["env"]
dataset = args["dataset"]
job_config = read_yaml_from_s3(f"{args['config_s3_prefix']}/job_configs/{dataset}.yaml")
job_config = deep_merge(job_config, job_config.get("environment_overrides", {}).get(env))
schema = read_yaml_from_s3(f"{args['config_s3_prefix']}/catalog/{dataset}.yaml")
logger.info(f"Processing dataset={dataset} env={env} input={args['input_path']}")

# ------------------------------------------------------------------ 2. Read raw file
source = job_config["source"]
df = (
    spark.read.option("header", str(source.get("header", True)).lower())
    .option("delimiter", source.get("delimiter", ","))
    .option("encoding", source.get("encoding", "UTF-8"))
    .csv(args["input_path"])  # everything is read as string; types come from the catalog schema
)
df = df.toDF(*[c.strip().lower() for c in df.columns])
raw_count = df.count()

# ------------------------------------------------------------------ 3. Enforce schema (catalog)
columns = schema["columns"]
expected = [c["name"] for c in columns]
missing = [c for c in expected if c not in df.columns]
if missing:
    raise ValueError(f"Input file is missing columns {missing}. Expected {expected}, got {df.columns}")

# Unparseable dates become NULL instead of raising, so several formats can be tried in turn.
spark.conf.set("spark.sql.legacy.timeParserPolicy", "CORRECTED")

rules = job_config.get("transformations", {})
case_rules = {"lowercase": F.lower, "uppercase": F.upper, "initcap": F.initcap}


def parse_with_formats(value, col, parser, default_format):
    formats = col.get("formats") or [col.get("format", default_format)]
    return F.coalesce(*[parser(value, fmt) for fmt in formats])


for col in columns:
    name = col["name"]
    value = F.trim(F.col(name)) if rules.get("trim_strings", True) else F.col(name)
    if rules.get("empty_string_as_null", False):
        value = F.when(value == "", None).otherwise(value)
    for rule, func in case_rules.items():
        if name in rules.get(rule, []):
            value = func(value)
    if col["type"] == "date":
        value = parse_with_formats(value, col, F.to_date, "yyyy-MM-dd")
    elif col["type"] == "timestamp":
        value = parse_with_formats(value, col, F.to_timestamp, "yyyy-MM-dd HH:mm:ss")
    else:
        value = value.cast(col["type"])
    df = df.withColumn(name, value)
df = df.select(expected)  # drop unexpected columns, fix column order

# ------------------------------------------------------------------ 4. Apply rules (job config)
not_null_cols = [c["name"] for c in columns if not c.get("nullable", True)]
valid_df = df.dropna(subset=not_null_cols) if not_null_cols else df
if rules.get("deduplicate_on"):
    valid_df = valid_df.dropDuplicates(rules["deduplicate_on"])

valid_count = valid_df.count()
rejected_pct = 0 if raw_count == 0 else round((raw_count - valid_count) * 100 / raw_count, 2)
max_reject = job_config.get("data_quality", {}).get("max_reject_percentage", 100)
logger.info(f"raw_rows={raw_count} valid_rows={valid_count} rejected_pct={rejected_pct} max_allowed={max_reject}")
if rejected_pct > max_reject:
    raise ValueError(f"Data quality check failed: {rejected_pct}% rows rejected (max {max_reject}%)")

# ------------------------------------------------------------------ 5. Audit columns + write
out = (
    valid_df.withColumn("source_file", F.lit(args["input_path"]))
    .withColumn("ingested_at", F.current_timestamp())
    .withColumn("load_date", F.date_format(F.current_date(), "yyyy-MM-dd"))
)

target = job_config["target"]
target_path = f"s3://{args['curated_bucket']}/curated/{dataset}/"
(
    out.write.mode(target.get("write_mode", "append"))
    .partitionBy(*target.get("partition_by", []))
    .option("compression", target.get("compression", "snappy"))
    .parquet(target_path)
)
logger.info(f"Wrote {valid_count} rows to {target_path}")

job.commit()

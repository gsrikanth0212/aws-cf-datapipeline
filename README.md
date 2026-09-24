# aws-cf-datapipeline

A small but realistic AWS data engineering project built with **CloudFormation** and deployed to
**dev → stage → prod** with **GitHub Actions**.

```
 s3://demo-learn-cloudform/raw/customers/customers_20260924.csv
            │  (S3 "Object Created" event)
            ▼
     EventBridge rule ──► Step Functions state machine
                            1. ValidateFile   (Lambda)  dataset known? .csv? not empty/too big?
                            2. IsFileValid?   (Choice)  no → FileRejected (Fail)
                            3. RunGlueJob     (Glue, waits for it to finish)
                                 reads  job_configs/customers.yaml  (how to process)
                                 reads  catalog/customers.yaml      (expected schema)
                                 writes s3://demo-datapipeline-dev-curated-<acct>/curated/customers/load_date=.../*.parquet
                            4. StartCrawler   (Glue crawler registers the table → query it in Athena)
```

## Project layout

```
aws-cf-datapipeline/
├── cf_cloudformation/          CloudFormation templates (infrastructure as code)
│   ├── main.yaml               root stack: wires the nested stacks together
│   ├── s3.yaml                 curated (output) bucket
│   ├── iam.yaml                roles for Glue, Lambda, Step Functions, EventBridge
│   ├── glue.yaml               Glue database, ETL job, crawler
│   ├── lambda.yaml             file-validation Lambda
│   ├── stepfunctions.yaml      state machine + EventBridge trigger
│   └── bootstrap/
│       └── github_oidc.yaml    ONE-TIME: role GitHub Actions uses to log in to AWS
├── configs/                    per-environment values (dev.yaml, stage.yaml, prod.yaml)
├── manifest.yaml               deployment "packing list": artifacts + datasets
├── job_configs/                HOW each dataset is processed (read by the Glue job)
├── catalog/                    WHAT each dataset looks like (schema contract)
├── glue_jobs/                  PySpark code (one generic job for all datasets)
├── lambda/validate_file/       Lambda code
├── buildspecs/                 AWS CodeBuild build specs (alternative CI runner)
├── scripts/                    validate.sh, deploy.sh, enable_s3_eventbridge.sh
├── docs/                       CONSOLE_DEPLOYMENT.md (deploy by clicking)
├── tests/                      unit tests for the Lambda
├── sample_data/                test files to drop into raw/
└── .github/workflows/          deploy.yml (+ reusable-deploy.yml)
```

## Why buildspecs, catalog, job_configs and manifest matter

| Folder / file | What it is | Why real projects use it |
|---|---|---|
| **configs/** | One file per environment with every value that changes: bucket names, Glue workers, log retention, tags. | The templates stay **identical** for dev/stage/prod, so what you tested in stage is exactly what goes to prod. Only the sizing and names differ. |
| **manifest.yaml** | Lists which folders to upload to S3 and which datasets exist. | The deploy script is driven by it, so adding a dataset or a new artifact folder is a one-line change, not a script change. It also feeds the Lambda's allow-list (`AllowedDatasets`), and `validate.sh` checks that every dataset has its config and schema **before** anything is deployed. |
| **job_configs/** | Per-dataset processing rules: CSV options, dedup keys, data-quality threshold, output format and partitioning, and per-environment overrides. | Lets **one generic Glue job** serve many datasets. Changing a rule (such as the reject threshold) is a config change reviewed in a PR, not a code change. Onboarding a new source takes a YAML file instead of a new job. |
| **catalog/** | Schema contract: column names, types, nullability, PII flags, owners. | The Glue job **enforces** it: missing columns fail the run, types are cast, and non-nullable columns are checked. Bad files are caught at ingestion instead of breaking reports later. It also documents the data (owner, PII) for governance. The crawler registers the curated table in the Glue Data Catalog so Athena can query it. |
| **buildspecs/** | Build instructions for **AWS CodeBuild** (used by AWS CodePipeline). | Many companies run CI/CD inside AWS rather than on GitHub, for example for private network access or company policy. Both buildspecs call the **same** `scripts/*.sh` that GitHub Actions calls, so the CI tool can change without changing how you deploy. You don't need them for this project's GitHub flow. They're here so you recognise them at work. |

**The main idea:** all the logic lives in `scripts/`. GitHub Actions, CodeBuild and your VS Code
terminal all run the same `validate.sh` / `deploy.sh`.

## How a deployment works (`scripts/deploy.sh dev`)

1. **Upload artifacts** from `manifest.yaml` → `s3://demo-learn-cloudform/artifacts/dev/{glue_jobs,job_configs,catalog}/`
   It also zips the Lambda code and uploads it as `lambda/validate_file-<hash>.zip`. The hash changes only when the code changes, which is what makes CloudFormation update the function.
2. **`aws cloudformation package`**: uploads the nested templates and writes `build/packaged-dev.yaml`
3. **`aws cloudformation deploy`**: creates or updates the stack `demo-datapipeline-dev` with the values from `configs/dev.yaml`. It uses a change set, so only the resources that changed are updated.

## Environments

| | dev | stage | prod |
|---|---|---|---|
| Listens on | `s3://demo-learn-cloudform/raw/` | `…/raw-stage/` | `…/raw-prod/` |
| Glue | 2 × G.1X | 5 × G.1X | 10 × G.2X |
| Log retention | 7 days | 30 days | 90 days |
| Deployed by | every merge to `main` | after dev succeeds | after stage succeeds **and a manual approval** |

> In real companies each environment is usually a **separate AWS account** with its own raw bucket.
> Because this demo uses one bucket, stage and prod listen on different prefixes. Otherwise one file
> would trigger all three pipelines.

---

## Setup

### 1. Local tools (VS Code terminal, macOS)

```bash
brew install awscli yq
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
aws configure            # or: aws sso login --profile <profile>
```

Recommended VS Code extensions: **AWS Toolkit**, **CloudFormation Linter** (kddejong.vscode-cfn-lint),
**YAML** (Red Hat), **GitHub Actions**, **Python**.
For `!Ref` / `!Sub` support in the YAML extension, add this to VS Code `settings.json`:

```json
"yaml.customTags": ["!Ref", "!Sub", "!GetAtt", "!Equals", "!If", "!Not", "!And", "!Or",
                    "!FindInMap", "!Join", "!Select", "!Split", "!ImportValue", "!Condition", "!Base64", "!GetAZs"]
```

### 2. One-time AWS setup

```bash
# a) Let the raw bucket send events to EventBridge (needed for the automatic trigger)
./scripts/enable_s3_eventbridge.sh demo-learn-cloudform us-east-1

# b) Create the role GitHub Actions will assume. Repeat per environment.
#    Use CreateOIDCProvider=false for the 2nd and 3rd environment in the same account.
aws cloudformation deploy \
  --template-file cf_cloudformation/bootstrap/github_oidc.yaml \
  --stack-name bootstrap-github-oidc-dev \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrg=<your-github-user> Environment=dev CreateOIDCProvider=true

aws cloudformation describe-stacks --stack-name bootstrap-github-oidc-dev \
  --query "Stacks[0].Outputs[?OutputKey=='DeployRoleArn'].OutputValue" --output text
```

### 3. GitHub setup

1. Push this folder to a GitHub repo named `aws-cf-datapipeline`.
2. **Settings → Environments**: create `dev`, `stage`, `prod`.
3. In each environment, add the secret **`AWS_DEPLOY_ROLE_ARN`**, set to the role ARN from step 2b.
4. On `prod`, enable **Required reviewers**. Prod deployments then pause until someone approves.

### 4. Deploy

- **From GitHub**: open a PR (runs validation only), then merge it (deploys dev → stage → prod).
  You can also go to **Actions → Deploy Data Pipeline → Run workflow** to deploy a single environment.
- **From your laptop**:
  ```bash
  ./scripts/validate.sh
  ./scripts/deploy.sh dev
  ```

> **Prefer clicking?** [docs/CONSOLE_DEPLOYMENT.md](docs/CONSOLE_DEPLOYMENT.md) walks through the same
> deployment in the AWS Console, with no scripts.

### 5. Test the pipeline

```bash
aws s3 cp sample_data/customers/customers_20260924.csv s3://demo-learn-cloudform/raw/customers/
aws s3 cp sample_data/orders/orders_20260924.csv       s3://demo-learn-cloudform/raw/orders/
```

Then open **Step Functions → demo-datapipeline-dev-pipeline** to watch the execution. After the
crawler finishes, query the data in **Athena** (database `demo_datapipeline_dev`).

The customers sample includes one row without an email and one duplicate row, which is 2 of 6 rows (33%)
rejected. That passes in dev (limit 50%) but would **fail** in stage/prod (limit 5%). This
shows the data-quality rule in `job_configs/customers.yaml` at work.

To try a rejected file, upload `something.json`, or put a file in `raw/unknown/`. The execution
ends in `FileRejected`, and the Lambda's reason is shown as the cause.

### Adding a new dataset (e.g. `products`)

1. `catalog/products.yaml`: columns and types
2. `job_configs/products.yaml`: processing rules
3. `manifest.yaml`: add it under `datasets`
4. Open a PR. No template or Glue code changes are needed.

### Clean up

```bash
aws cloudformation delete-stack --stack-name demo-datapipeline-dev
```
The curated bucket has `DeletionPolicy: Retain`, so your data survives stack deletion. Empty and delete
the bucket manually if you really want it gone.

## Costs

Glue is the only notable cost. It is billed per DPU-hour while a job runs; a small dev run costs a few cents.
Lambda, Step Functions, EventBridge and S3 cost almost nothing at this volume. Nothing runs when no files arrive.

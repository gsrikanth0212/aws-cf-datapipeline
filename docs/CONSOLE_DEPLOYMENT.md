# Deploying from the AWS Console (no scripts)

This guide deploys the **dev** environment by clicking through the AWS Console. It uses the
same templates as `scripts/deploy.sh`, but you do each step by hand, which is a good way to see
what the script automates.

## The one difference: 5 separate stacks instead of 1 nested stack

`main.yaml` points at `./s3.yaml`, `./iam.yaml` and so on, which are **files on your laptop**.
The console can't read them. Only `aws cloudformation package` can upload them for you.

So in the console you deploy the five child templates **one at a time, in order**. You copy each
stack's **Outputs** into the next stack's **Parameters**. That's exactly what `main.yaml` does
automatically with `!GetAtt S3Stack.Outputs...`.

```
1. s3.yaml  ──CuratedBucketName──► 2. iam.yaml ──role ARNs──► 3. glue.yaml ──job/crawler names──┐
                                                          └──► 4. lambda.yaml ──function ARN──────┤
                                                                                                  ▼
                                                                                 5. stepfunctions.yaml
```

All values below are for **dev** and come from `configs/dev.yaml`.
Region: **US East (N. Virginia) us-east-1**. Check the region picker (top right) before each step.

---

## Step 1: Prepare the Lambda zip (Finder)

1. In Finder, open `aws-cf-datapipeline/lambda/validate_file/`.
2. Right-click **`app.py`** (the file, **not** the folder) → **Compress "app.py"** → you get `app.zip`.
3. Rename it to **`validate_file-v1.zip`**.

> Zip the file, not the folder. Lambda looks for `app.py` at the root of the zip
> (`Handler: app.lambda_handler`). If you zip the folder, you'll get "Unable to import module 'app'".

## Step 2: Upload artifacts (S3 Console)

S3 → **demo-learn-cloudform** → **Create folder** `artifacts`, then inside it create `dev`.
Open `artifacts/dev/` and upload:

| Upload (Add folder / Add files) | Ends up at |
|---|---|
| folder `glue_jobs` | `artifacts/dev/glue_jobs/raw_to_curated.py` |
| folder `job_configs` | `artifacts/dev/job_configs/customers.yaml`, `orders.yaml` |
| folder `catalog` | `artifacts/dev/catalog/customers.yaml`, `orders.yaml` |
| create folder `lambda`, upload `validate_file-v1.zip` into it | `artifacts/dev/lambda/validate_file-v1.zip` |

Check the paths match exactly. The Glue job and the templates look for these exact keys.

## Step 3: Turn on EventBridge for the bucket (one time)

S3 → **demo-learn-cloudform** → **Properties** tab → scroll to **Event notifications** →
**Amazon EventBridge** → **Edit** → **On** → **Save changes**.

This makes S3 announce every new file to EventBridge. The rule created in Step 8 listens for those announcements.

## Step 4: Stack 1, S3 (curated bucket) (optional)

> **Using one bucket for everything?** Skip this stack. In Steps 5 and 6, enter `demo-learn-cloudform`
> as **CuratedBucketName**. Glue then writes to `s3://demo-learn-cloudform/curated/`. The IAM role only
> lets Glue write to the `curated/` and `glue-temp/` folders, so your `raw/` files are safe.

CloudFormation → **Create stack** → **With new resources (standard)** →
**Choose an existing template** → **Upload a template file** → `cf_cloudformation/s3.yaml` → Next

| Field | Value |
|---|---|
| Stack name | `demo-datapipeline-dev-s3` |
| ProjectName | `demo-datapipeline` |
| Environment | `dev` |

Next → on **Configure stack options**, add tags `Project=demo-datapipeline`, `Environment=dev` → Next → **Submit**.

Wait for **CREATE_COMPLETE**. Then open the **Outputs** tab and copy **CuratedBucketName**
(`demo-datapipeline-dev-curated-<ACCOUNT_ID>`).

## Step 5: Stack 2, IAM roles

Create stack → upload `cf_cloudformation/iam.yaml`

| Field | Value |
|---|---|
| Stack name | `demo-datapipeline-dev-iam` |
| ProjectName | `demo-datapipeline` |
| Environment | `dev` |
| RawBucketName | `demo-learn-cloudform` |
| RawPrefix | `raw/` |
| ArtifactBucketName | `demo-learn-cloudform` |
| ArtifactPrefix | `artifacts/dev` |
| CuratedBucketName | *(from Stack 1 outputs)* |

On the **Review** page, tick **"I acknowledge that AWS CloudFormation might create IAM resources with
custom names"**. This is the console version of `--capabilities CAPABILITY_NAMED_IAM`. Then **Submit**.

When it's done, open **Outputs** and keep this tab open. You'll need **GlueRoleArn**, **LambdaRoleArn**,
**StepFunctionsRoleArn** and **EventBridgeRoleArn**.

## Step 6: Stack 3, Glue

Create stack → upload `cf_cloudformation/glue.yaml`

| Field | Value |
|---|---|
| Stack name | `demo-datapipeline-dev-glue` |
| ProjectName / Environment | `demo-datapipeline` / `dev` |
| GlueRoleArn | *(from Stack 2)* |
| ArtifactBucketName | `demo-learn-cloudform` |
| ArtifactPrefix | `artifacts/dev` |
| CuratedBucketName | *(from Stack 1)* |
| GlueDatabaseName | `demo_datapipeline_dev` |
| GlueVersion | `4.0` |
| GlueWorkerType | `G.1X` |
| GlueNumberOfWorkers | `2` |
| GlueTimeoutMinutes | `30` |

Outputs you'll need: **GlueJobName**, **CrawlerName**.

## Step 7: Stack 4, Lambda

Create stack → upload `cf_cloudformation/lambda.yaml`

| Field | Value |
|---|---|
| Stack name | `demo-datapipeline-dev-lambda` |
| ProjectName / Environment | `demo-datapipeline` / `dev` |
| LambdaRoleArn | *(from Stack 2)* |
| LambdaMemorySize | `128` |
| LambdaCodeS3Bucket | `demo-learn-cloudform` |
| LambdaCodeS3Key | `artifacts/dev/lambda/validate_file-v1.zip` |
| RawPrefix | `raw/` |
| AllowedDatasets | `customers,orders` |
| AllowedExtensions | `.csv` |
| MaxFileSizeMB | `100` |
| LogRetentionDays | `7` |

Output you'll need: **ValidateFunctionArn**.

Quick test: Lambda → `demo-datapipeline-dev-validate-file` → **Test** tab → paste this event → **Test**:
```json
{"bucket": "demo-learn-cloudform", "key": "raw/customers/customers_20260901.csv", "size": 16927}
```
You should get `"is_valid": true, "dataset": "customers"`.

## Step 8: Stack 5, Step Functions + trigger

Create stack → upload `cf_cloudformation/stepfunctions.yaml`

| Field | Value |
|---|---|
| Stack name | `demo-datapipeline-dev-sfn` |
| ProjectName / Environment | `demo-datapipeline` / `dev` |
| StepFunctionsRoleArn | *(from Stack 2)* |
| EventBridgeRoleArn | *(from Stack 2)* |
| ValidateFunctionArn | *(from Stack 4)* |
| GlueJobName | *(from Stack 3)* |
| CrawlerName | *(from Stack 3)* |
| RawBucketName | `demo-learn-cloudform` |
| RawPrefix | `raw/` |
| LogRetentionDays | `7` |
| EnableS3Trigger | `true` |

## Step 9: Run the pipeline for your existing file

Your file was uploaded **before** the trigger existed, so it didn't start anything. Start the pipeline by hand:

Step Functions → **demo-datapipeline-dev-pipeline** → **Start execution** → paste this input
(the same shape as the event S3 sends) → **Start execution**:

```json
{
  "detail": {
    "bucket": { "name": "demo-learn-cloudform" },
    "object": { "key": "raw/customers/customers_20260901.csv", "size": 16927 }
  }
}
```

The **Graph view** shows each step turning green. Click a step to see its input and output. The Glue step
takes about 1–2 minutes (Glue → ETL jobs → the job → **Runs** tab shows logs).

From now on, **uploading a new file** to `raw/customers/` starts an execution automatically.

## Step 10: Query the result

1. Wait about a minute after the execution succeeds. The crawler is creating the table.
   Check under Glue → **Crawlers** → the crawler's status.
2. Athena → **Query editor**. The first time, go to **Settings** and set the query result location to
   `s3://demo-learn-cloudform/athena-results/`.
3. Database `demo_datapipeline_dev`, then run:
   ```sql
   SELECT segment, source_system, count(*) AS customers
   FROM customers
   GROUP BY 1, 2
   ORDER BY 3 DESC;
   ```

---

## Making changes later

| What changed | What to do in the console |
|---|---|
| Glue script / job config / catalog | Just **re-upload the file** to `artifacts/dev/...`. The next run picks it up, and no stack update is needed. |
| Lambda code | Zip again as **`validate_file-v2.zip`** (a **new name**), upload it, then CloudFormation → Lambda stack → **Update** → **Use existing template** → change `LambdaCodeS3Key`. If you reuse the same name, CloudFormation sees no change and keeps the old code. |
| A template (e.g. more Glue workers) | CloudFormation → the stack → **Update** → **Replace existing template** or change parameters. Before submitting, review the **change set preview**. It lists what will be modified or **replaced**. |

> **Avoid editing resources directly** (e.g. changing the Glue job in the Glue console). The template no
> longer matches reality. This is called **drift**, and it can be checked with CloudFormation → stack →
> **Stack actions → Detect drift**. The next stack update will overwrite your manual edit.

## Deleting

Delete the stacks in **reverse order**: `-sfn`, `-lambda`, `-glue`, `-iam`, `-s3`.
The curated bucket is kept (`DeletionPolicy: Retain`) so no data is lost. Empty it and delete it
yourself in S3 if you want it gone.

## Console vs scripts: which one should you use?

**Pick one method per environment.** The console stacks and `./scripts/deploy.sh dev` create
resources with the **same names** (roles, bucket, Glue job). If you've deployed dev from the console and
then run the script, it fails with "already exists". To switch to the script, delete the five console
stacks, delete the curated bucket, and then run the script.

| | Console | Scripts / GitHub Actions |
|---|---|---|
| Good for | Learning, one-off experiments, **looking at and debugging** what's deployed | Every real deployment |
| Repeatable for stage/prod? | No. You'd retype ~40 values per environment | Yes. Values come from `configs/<env>.yaml` |
| Reviewable? | No. Nobody sees what you clicked | Yes. Every change is a pull request |
| Speed | ~30 minutes of clicking | ~5 minutes, unattended |

In real teams, people use the console to *look* (stack events, Step Functions graph, Glue logs,
Athena) and let the pipeline do the *deploying*. Doing it by hand once is still the best way to
understand what `deploy.sh` does for you.

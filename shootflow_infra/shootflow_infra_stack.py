from aws_cdk import (
    Stack,
    RemovalPolicy,
    Duration,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_lambda as _lambda,
    aws_s3_notifications as s3n,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_sqs as sqs,  # ADD
    aws_secretsmanager as secretsmanager,  # ADD
)
from aws_cdk import aws_lambda_event_sources as lambda_event_sources  # ADD
from aws_cdk import aws_events as events  # ADD
from aws_cdk import aws_events_targets as events_targets  # ADD
import os
import re
from typing import Optional
from constructs import Construct
from aws_cdk import Stack as CdkStack

class ShootflowInfraStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account_id = CdkStack.of(self).account

        ecr_repo = ecr.Repository(self, "ShootflowEcrRepository", repository_name="shootflow-ecr-repo")

        ecr_publish_role = iam.Role(
            self,
            "ShootflowEcrPublishRole",
            assumed_by=iam.FederatedPrincipal(
                f"arn:aws:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com",
                {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": "repo:cleopold73/shootflow*:*"
                    }
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
            description="Role for GitHub Actions to publish Docker images to ECR"
        )
        ecr_publish_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    'ecr:GetAuthorizationToken',
                    'ecr:BatchCheckLayerAvailability',
                    'ecr:GetDownloadUrlForLayer',
                    'ecr:BatchGetImage',
                    'ecr:InitiateLayerUpload',
                    'ecr:UploadLayerPart',
                    'ecr:CompleteLayerUpload',
                    'ecr:PutImage',
                    'ecr:CreateRepository',
                    'ecr:DescribeRepositories',
                ],
                resources=[ecr_repo.repository_arn],
            )
        )

        ecr_publish_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"]
            )
        )

        upload_bucket = s3.Bucket(
            self, "ShootflowUploadBucket",
            bucket_name="shootflowupload",
            removal_policy=RemovalPolicy.DESTROY
        )

        # ADD: DLQ for failed SQS messages
        dlq = sqs.Queue(
            self, "IngestDlq",
            retention_period=Duration.days(14)
        )

        # ADD: Main SQS queue receiving S3 notifications
        ingest_queue = sqs.Queue(
            self, "IngestQueue",
            # Must be greater than the Lambda timeout (set below to 60s)
            visibility_timeout=Duration.seconds(120),
            dead_letter_queue=sqs.DeadLetterQueue(
                queue=dlq,
                max_receive_count=5
            )
        )

        upload_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.SqsDestination(ingest_queue)
            # , s3.NotificationKeyFilter(prefix="uploads/", suffix=".jpg")  # optional filters
        )

        metadata_table = dynamodb.Table(
            self, "ShootFlowPhotoMetadata",
            partition_key=dynamodb.Attribute(name="photoId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ADD: GSI with partition key 'status' and sort key 'photoId'
        metadata_table.add_global_secondary_index(
            index_name="status-photoId-gsi",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="photoId", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # Read the image URI from context/env (provided by the workflow)
        image_uri = self.node.try_get_context("imageUri") or os.getenv("IMAGE_URI")
        if not image_uri:
            # Read from repo file if present
            repo_dir = os.path.dirname(os.path.abspath(__file__))
            uri_file = os.path.join(repo_dir, "image_uri.txt")
            try:
                with open(uri_file, "r", encoding="utf-8") as f:
                    contents = f.read().strip()
                    image_uri = contents if contents else None
            except FileNotFoundError:
                image_uri = None

        def parse_repo_and_ref(uri: str) -> tuple[Optional[str], Optional[str]]:
            # Formats:
            # <acct>.dkr.ecr.<region>.amazonaws.com/repo:tag
            # <acct>.dkr.ecr.<region>.amazonaws.com/repo@sha256:...
            m = re.match(r"^[^/]+/([^:@]+)(?::([^@]+))?(?:@(sha256:[0-9a-fA-F]{64}))?$", uri or "")
            if not m:
                return None, None
            repo_name = m.group(1)
            tag = m.group(2)
            digest = m.group(3)
            return repo_name, (digest or tag)

        # Determine target repo and tag/digest. Always create Lambdas; choose image source conditionally.
        target_repo = None
        tag_or_digest: Optional[str] = None
        if image_uri:
            repo_name, tag_or_digest = parse_repo_and_ref(image_uri)
            if not repo_name or not tag_or_digest:
                raise ValueError(f"Unable to parse repository and tag/digest from image URI: {image_uri}")

            # Use the created repo if names match; otherwise import the target repo by name
            if repo_name == ecr_repo.repository_name:
                target_repo = ecr_repo
            else:
                target_repo = ecr.Repository.from_repository_name(
                    self, "ImportedImageRepo", repository_name=repo_name
                )
        else:
            # No image specified: keep Lambdas using a stable default tag on our repo.
            # You can override via context: -c defaultImageTag=prod or env DEFAULT_IMAGE_TAG
            target_repo = ecr_repo
            tag_or_digest = self.node.try_get_context("defaultImageTag") or os.getenv("DEFAULT_IMAGE_TAG") or "shootflow-services-latest"

        code = _lambda.DockerImageCode.from_ecr(
            repository=target_repo,
            tag_or_digest=tag_or_digest
        )

        # noinspection PyTypeChecker
        ingest_fn: _lambda.IFunction = _lambda.DockerImageFunction(
            self, "IngestFunction",
            code=code,
            timeout=Duration.seconds(60),
            function_name=f"{CdkStack.of(self).stack_name}-Ingest",
            environment={
                "SHOOTFLOW_DDB_TABLE": metadata_table.table_name,
                "SHOOTFLOW_S3_BUCKET": upload_bucket.bucket_name,
                "LAMBDA_TARGET": "shootflow.handlers.s3_ingest:handler",
                "LAMBDA_ADAPTER": "sqs_s3",
            }
        )
        metadata_table.grant_read_write_data(ingest_fn)
        upload_bucket.grant_read(ingest_fn)

        # CHANGE: Connect SQS -> Lambda (replace direct S3 -> Lambda)
        ingest_fn.add_event_source(
            lambda_event_sources.SqsEventSource(
                ingest_queue,
                batch_size=10,                         # tune as needed
                max_batching_window=Duration.seconds(5)  # optional
            )
        )
        # Second Lambda: Flickr Poster
        # noinspection PyTypeChecker
        flickr_poster_fn: _lambda.IFunction = _lambda.DockerImageFunction(
            self, "FlickrPosterFunction",
            code=code,
            timeout=Duration.seconds(600),
            memory_size=512,
            function_name=f"{CdkStack.of(self).stack_name}-FlickrPoster",
            environment={
                "SHOOTFLOW_DDB_TABLE": metadata_table.table_name,
                "SHOOTFLOW_S3_BUCKET": upload_bucket.bucket_name,
                "LAMBDA_TARGET": "shootflow.handlers.flickr_poster:handler",
                "LAMBDA_ADAPTER": "passthrough",
            }
        )
        metadata_table.grant_read_write_data(flickr_poster_fn)
        upload_bucket.grant_read(flickr_poster_fn)

        # Grant Flickr poster Lambda read access to the "FLICKR-API" secret
        flickr_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "FlickrApiSecret", "FLICKR-API"
        )
        flickr_secret.grant_read(flickr_poster_fn)

        # Grant Flickr poster Lambda read access to the "OPENAI-API" secret
        openai_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "OpenAiApiSecret", "OPENAI-API"
        )
        openai_secret.grant_read(flickr_poster_fn)

        # Trigger Flickr Poster daily around 8am US Central (14:00 UTC)
        events.Rule(
            self, "FlickrPosterScheduleRule",
            schedule=events.Schedule.cron(minute="0", hour="14"),
            targets=[events_targets.LambdaFunction(flickr_poster_fn)]
        )

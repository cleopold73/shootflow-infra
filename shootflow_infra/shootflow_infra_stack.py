from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_lambda as _lambda,
    aws_s3_notifications as s3n,
    aws_ecr as ecr,
    aws_iam as iam
)
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

        metadata_table = dynamodb.Table(
            self, "ShootFlowPhotoMetadata",
            partition_key=dynamodb.Attribute(name="photoId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Read the image URI from context/env (provided by the workflow)
        image_uri = self.node.try_get_context("imageUri") or os.getenv("IMAGE_URI")

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

            code = _lambda.DockerImageCode.from_ecr(
                repository=target_repo,
                tag_or_digest=tag_or_digest
            )

            # noinspection PyTypeChecker
            ingest_fn: _lambda.IFunction = _lambda.DockerImageFunction(
                self, "IngestFunction",
                code=code,
                environment={
                    "SHOOTFLOW_DDB_TABLE": metadata_table.table_name,
                }
            )
            metadata_table.grant_read_write_data(ingest_fn)
            upload_bucket.add_event_notification(
                s3.EventType.OBJECT_CREATED,
                s3n.LambdaDestination(ingest_fn)
            )
            upload_bucket.grant_read(ingest_fn)

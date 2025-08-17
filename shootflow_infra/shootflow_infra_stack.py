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

class ShootflowInfraStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ecr_repo = ecr.Repository(self, "ShootflowEcrRepository", repository_name="shootflow-ecr-repo")

        ecr_publish_role = iam.Role(
            self,
            "ShootflowEcrPublishRole",
            assumed_by=iam.FederatedPrincipal(
                "arn:aws:iam::477763023369:oidc-provider/token.actions.githubusercontent.com",
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

        def extract_tag_or_digest(uri: str) -> Optional[str]:
            # Accepts .../repo:tag or .../repo@sha256:...
            if "@sha256:" in uri:
                m = re.search(r"@sha256:[0-9a-fA-F]{64}$", uri)
                return m.group(0)[1:] if m else None  # return "sha256:..."
            # Fallback to tag
            m = re.search(r":([^:@]+)$", uri)
            return m.group(1) if m else None

        if image_uri:
            tag_or_digest = extract_tag_or_digest(image_uri)
            if not tag_or_digest:
                raise ValueError(f"Unable to extract tag or digest from image URI: {image_uri}")

            # Use the existing ECR repo and the extracted tag/digest
            code = _lambda.EcrImageCode.from_ecr_repository(
                repository=ecr_repo,
                tag_or_digest=tag_or_digest
            )

            ingest_fn: _lambda.IFunction = _lambda.DockerImageFunction(
                self, "IngestFunction",
                code=code,
                environment={
                    "TABLE_NAME": metadata_table.table_name,
                }
            )
            metadata_table.grant_read_write_data(ingest_fn)
            upload_bucket.add_event_notification(
                s3.EventType.OBJECT_CREATED,
                s3n.LambdaDestination(ingest_fn)
            )
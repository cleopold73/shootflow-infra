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
        if image_uri:
            ingest_fn = _lambda.DockerImageFunction(
                self, "IngestFunction",
                code=_lambda.DockerImageCode.from_image_uri(image_uri=image_uri),
                environment={
                    "TABLE_NAME": metadata_table.table_name,
                }
            )
            metadata_table.grant_read_write_data(ingest_fn)
            # Hook up events if/when needed:
            upload_bucket.add_event_notification(s3.EventType.OBJECT_CREATED, s3n.LambdaDestination(ingest_fn))
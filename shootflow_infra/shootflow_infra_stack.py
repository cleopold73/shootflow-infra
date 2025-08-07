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

from constructs import Construct

class ShootflowInfraStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ecr_repo = ecr.Repository(self, "ShootflowEcrRepository", repository_name="shootflow-ecr-repo")

        ecr_publish_role = iam.Role(
            self,
            "ShootflowEcrPublishRole",
            # The trust policy for GitHub OIDC
            assumed_by=iam.FederatedPrincipal(
                "arn:aws:iam::477763023369:oidc-provider/token.actions.githubusercontent.com",
                {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": "repo:cleopold73/shootflow-infra:*"
                    }
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
            description="Role for GitHub Actions to publish Docker images to ECR"
        )


        ecr_publish_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:CompleteLayerUpload",
                    "ecr:DescribeImages",
                    "ecr:DescribeRepositories",
                    "ecr:InitiateLayerUpload",
                    "ecr:PutImage",
                    "ecr:UploadLayerPart",
                    "ecr:GetAuthorizationToken"
                ],
                resources=[ecr_repo.repository_arn],
            )
        )

        ecr_publish_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:GetAuthorizationToken"
                ],
                resources=["*"]  # Authorization tokens are not specific to the repo
            )
        )




        upload_bucket = s3.Bucket(
            self,"ShootflowUploadBucket",  # Logical ID for this resource
            bucket_name="shootflowupload",  # Name of the bucket
            removal_policy=RemovalPolicy.DESTROY
            # Optional: Automatically destroy the bucket if the stack is deleted
        )

        metadata_table = dynamodb.Table(
            self, "ShootFlowPhotoMetadata",
            partition_key=dynamodb.Attribute(name="photoId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,       # dev only—use RETAIN for prod
        )

        # ingest_fn = _lambda.DockerImageFunction(
        #     self, "IngestFunction",
        #     code=_lambda.DockerImageCode.from_image_asset(
        #         "../shootflow-services/functions/ingest"
        #     ),
        #     environment={
        #         "TABLE_NAME": metadata_table.table_name,
        #     }
        # )
        # metadata_table.grant_read_write_data(ingest_fn)
        # upload_bucket.add_event_notification(s3.EventType.OBJECT_CREATED, s3n.LambdaDestination(ingest_fn))
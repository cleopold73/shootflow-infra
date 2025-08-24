from aws_cdk import (
    Stack,
    Tags,
    aws_ecr as ecr,
    aws_iam as iam,
)
from constructs import Construct
from aws_cdk import Stack as CdkStack


class ShootflowEcrStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account_id = CdkStack.of(self).account

        Tags.of(self).add("Component", "ECR")

        # Single shared ECR repository
        repo = ecr.Repository(self, "ShootflowEcrRepository", repository_name="shootflow-ecr-repo")

        # Role for GitHub Actions to publish Docker images to ECR
        ecr_publish_role = iam.Role(
            self,
            "ShootflowEcrPublishRole",
            assumed_by=iam.FederatedPrincipal(
                f"arn:aws:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com",
                {
                    "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
                    "StringLike": {"token.actions.githubusercontent.com:sub": "repo:cleopold73/shootflow*:*"},
                },
                "sts:AssumeRoleWithWebIdentity",
            ),
            description="Role for GitHub Actions to publish Docker images to ECR",
        )
        ecr_publish_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:PutImage",
                    "ecr:CreateRepository",
                    "ecr:DescribeRepositories",
                ],
                resources=[repo.repository_arn],
            )
        )
        ecr_publish_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )

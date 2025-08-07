from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_s3 as s3,  # Import the S3 module
)

from constructs import Construct

class ShootflowInfraStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        shootflow_upload_bucket = s3.Bucket(
            self,
            "ShootflowUploadBucket",  # Logical ID for this resource
            bucket_name="shootflow-upload",  # Name of the bucket
            removal_policy=RemovalPolicy.DESTROY
            # Optional: Automatically destroy the bucket if the stack is deleted
        )


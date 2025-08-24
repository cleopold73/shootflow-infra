#!/usr/bin/env python3
import os

import aws_cdk as cdk

from shootflow_infra.shootflow_infra_stack import ShootflowInfraStack
from shootflow_infra.ecr_stack import ShootflowEcrStack


app = cdk.App()

# Always suffix the stack id with environment (e.g., ShootflowInfraStack-production, -development)
deploy_env = app.node.try_get_context("env") or os.getenv("DEPLOY_ENV") or "production"
stack_id = f"ShootflowInfraStack-{deploy_env}"

# Shared ECR stack (deployed independently) — treat as production-owned
ShootflowEcrStack(app, "ShootflowEcrStack-production", stack_name="ShootflowEcrStack-production")

# Environment-specific application stack
ShootflowInfraStack(
    app,
    stack_id,
    stack_name=stack_id,
    # If you don't specify 'env', this stack will be environment-agnostic.
    # Account/Region-dependent features and context lookups will not work,
    # but a single synthesized template can be deployed anywhere.
    #env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION')),
    #env=cdk.Environment(account='123456789012', region='us-east-1'),
)

app.synth()
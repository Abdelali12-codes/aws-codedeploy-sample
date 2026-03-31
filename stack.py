import aws_cdk as cdk
from aws_cdk import (
    aws_codedeploy as codedeploy,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class CodeDeployEC2Stack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # ── IAM role for EC2 instances (CodeDeploy agent needs this) ────
        instance_role = iam.Role(
            self, "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                # Allows the CodeDeploy agent on the instance to pull
                # the revision from S3 and report deployment status
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
            ],
        )

        artifact_bucket = s3.Bucket.from_bucket_name(
            self, "ArtifactBucket", "my-artifact-bucket"
        )
        artifact_bucket.grant_read(instance_role)

        # ── VPC + EC2 instance ──────────────────────────────────────────
        vpc = ec2.Vpc(self, "Vpc", max_azs=1, nat_gateways=0)

        instance = ec2.Instance(
            self, "AppInstance",
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, ec2.InstanceSize.MICRO
            ),
            machine_image=ec2.AmazonLinuxImage(
                generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2023
            ),
            vpc=vpc,
            role=instance_role,
            # Install CodeDeploy agent on first boot
            user_data=ec2.UserData.custom(
                "#!/bin/bash\n"
                "yum install -y ruby wget\n"
                "wget https://aws-codedeploy-us-east-1.s3.amazonaws.com/latest/install\n"
                "chmod +x ./install && ./install auto\n"
                "systemctl start codedeploy-agent\n"
            ),
        )
        # Tag used by CodeDeploy to identify deployment targets
        cdk.Tags.of(instance).add("Environment", "production")

        # ── CodeDeploy Application ──────────────────────────────────────
        application = codedeploy.ServerApplication(
            self, "Application",
            application_name="my-ec2-app",
        )

        # ── Deployment Group ────────────────────────────────────────────
        # A deployment group = the set of EC2 instances to deploy to
        deployment_group = codedeploy.ServerDeploymentGroup(
            self, "DeploymentGroup",
            application=application,
            deployment_group_name="my-ec2-deployment-group",
            # Target instances by tag
            ec2_instance_tags=codedeploy.InstanceTagSet(
                {"Environment": ["production"]}
            ),
            # IN_PLACE: stop app → deploy → restart (same instances)
            # BLUE_GREEN: launch new instances → shift traffic → terminate old
            deployment_config=codedeploy.ServerDeploymentConfig.ONE_AT_A_TIME,
            # Automatically roll back if deployment fails
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
            ),
        )

        cdk.CfnOutput(self, "AppName", value=application.application_name)
        cdk.CfnOutput(self, "DGName", value=deployment_group.deployment_group_name)

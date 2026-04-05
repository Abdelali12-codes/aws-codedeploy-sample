import aws_cdk as cdk
from aws_cdk import (
    aws_codedeploy as codedeploy,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3,
    aws_codepipeline as codepipeline,
    aws_codebuild as codebuild,
)
from constructs import Construct


class CodeDeployEC2Stack(cdk.Stack):
    """
    Use case: Hotfix files manually uploaded to EC2 instances go missing
              after a failed deployment + auto-rollback.

    Root cause:
      1. Developer manually uploads hotfix files to /var/www/my-app/hotfix/
         on all instances (not included in the current revision).
      2. Next release includes the hotfix files in the new revision.
      3. Deployment fails for an unrelated reason.
      4. CodeDeploy auto-rolls back to the PREVIOUS revision.
      5. The previous revision never had the hotfix files, so CodeDeploy
         removes them during rollback → application breaks.

    Why it happens:
      - Default file_exists_behavior is OVERWRITE.
      - On rollback, CodeDeploy re-deploys the previous revision, which
        does NOT include the hotfix files → they are deleted from the instance.
      - Even if the new revision had file_exists_behavior: RETAIN for those
        files, rollback re-runs the OLD appspec which has no RETAIN rule.

    Correct fix (two layers):
      Layer 1 — Immediate:
        Set file_exists_behavior: RETAIN on the hotfix files entry in the
        NEW revision's appspec. This prevents OVERWRITE during forward deploy,
        but does NOT protect against rollback (old appspec has no RETAIN).

      Layer 2 — Permanent (the real fix):
        ALWAYS include hotfix files in the revision from the start.
        Manual file uploads to instances should never be the source of truth.
        Use the revision as the single source of truth for all instance files.
        If a hotfix is urgent, create a new revision that includes it and
        deploy that revision — do not manually place files on instances.

    file_exists_behavior options:
      OVERWRITE  → replace existing file with artifact version (default)
      RETAIN     → keep existing file, ignore artifact version
      DISALLOW   → fail deployment if file already exists
    """

    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # ── IAM role for EC2 instance ───────────────────────────────────
        instance_role = iam.Role(
            self, "InstanceRole",
            role_name="ec2-codedeploy-instance-role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )

        # ── VPC + Ubuntu EC2 instance ───────────────────────────────────
        vpc = ec2.Vpc(self, "Vpc", max_azs=1, nat_gateways=0)

        sg = ec2.SecurityGroup(self, "Sg", vpc=vpc)
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(8080))
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "export DEBIAN_FRONTEND=noninteractive",
            "apt-get update -qq",
            "apt-get install -y -qq ruby wget python3 python3-pip python3-venv iproute2 curl",
            "wget -q https://aws-codedeploy-us-east-1.s3.amazonaws.com/latest/install",
            "chmod +x ./install",
            "./install auto",
            "systemctl enable codedeploy-agent",
            "systemctl start codedeploy-agent",
            # Pre-create the hotfix directory so RETAIN has something to protect
            "mkdir -p /var/www/my-app/hotfix",
        )

        instance = ec2.Instance(
            self, "AppInstance",
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO),
            machine_image=ec2.MachineImage.from_ssm_parameter(
                "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
            ),
            vpc=vpc,
            role=instance_role,
            security_group=sg,
            user_data=user_data,
        )
        cdk.Tags.of(instance).add("Environment", "production")

        # ── CodeDeploy Application + Deployment Group ───────────────────
        application = codedeploy.ServerApplication(
            self, "Application",
            application_name="ec2-app",
        )

        codedeploy_role = iam.Role(
            self, "CodeDeployRole",
            role_name="ec2-codedeploy-service-role",
            assumed_by=iam.ServicePrincipal("codedeploy.amazonaws.com"),
            # Do NOT attach AWSCodeDeployRole here manually.
            # ServerDeploymentGroup automatically attaches
            # arn:aws:iam::aws:policy/service-role/AWSCodeDeployRole
            # to whatever role you pass via the role= parameter.
            # Attaching it here too causes a duplicate ARN in
            # ManagedPolicyArns which fails CloudFormation validation.
        )

        deployment_group = codedeploy.ServerDeploymentGroup(
            self, "DeploymentGroup",
            application=application,
            deployment_group_name="ec2-deployment-group",
            role=codedeploy_role,
            ec2_instance_tags=codedeploy.InstanceTagSet({"Environment": ["production"]}),
            deployment_config=codedeploy.ServerDeploymentConfig.ONE_AT_A_TIME,
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
            ),
        )

        cdk.CfnOutput(self, "AppName", value=application.application_name)
        cdk.CfnOutput(self, "DGName", value=deployment_group.deployment_group_name)
        cdk.CfnOutput(self, "InstanceId", value=instance.instance_id)

        # ── Artifact bucket (read from cdk.json context) ────────────────
        # Set the bucket name in cdk.json under "artifact_bucket_name",
        # or pass it at synth time: cdk synth -c artifact_bucket_name=my-bucket
        # The bucket must already exist — we import it by name, not create it.
        artifact_bucket_name = self.node.get_context("artifact_bucket_name")
        artifact_bucket = s3.Bucket.from_bucket_name(
            self, "ArtifactBucket", artifact_bucket_name
        )

        # ── CodeBuild ───────────────────────────────────────────────────
        build_role = iam.Role(
            self, "BuildRole",
            role_name="ec2-codebuild-role",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
        )
        artifact_bucket.grant_read_write(build_role)
        build_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=["*"],
        ))

        build_project = codebuild.CfnProject(
            self, "BuildProject",
            name="ec2-build",
            service_role=build_role.role_arn,
            artifacts=codebuild.CfnProject.ArtifactsProperty(type="CODEPIPELINE"),
            environment=codebuild.CfnProject.EnvironmentProperty(
                type="LINUX_CONTAINER",
                compute_type="BUILD_GENERAL1_SMALL",
                image="aws/codebuild/standard:7.0",
            ),
            source=codebuild.CfnProject.SourceProperty(
                type="CODEPIPELINE",
                build_spec="\n".join([
                    "version: 0.2",
                    "phases:",
                    "  build:",
                    "    commands:",
                    "      - echo Build started",
                    "artifacts:",
                    "  files:",
                    "    - appspec.yml",
                    "    - scripts/**/*",
                    "    - app/**/*",
                    # hotfix/ is now ALWAYS included in the revision —
                    # this is the permanent fix: never rely on manual uploads
                    "    - hotfix/**/*",
                ]),
            ),
        )

        # ── Pipeline role ───────────────────────────────────────────────
        pipeline_role = iam.Role(
            self, "PipelineRole",
            role_name="ec2-codepipeline-role",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
        )
        artifact_bucket.grant_read_write(pipeline_role)
        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=["codebuild:BatchGetBuilds", "codebuild:StartBuild"],
            resources=["*"],
        ))
        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "codedeploy:CreateDeployment", "codedeploy:GetDeployment",
                "codedeploy:GetDeploymentConfig", "codedeploy:GetApplicationRevision",
                "codedeploy:RegisterApplicationRevision",
            ],
            resources=["*"],
        ))

        # ── CfnPipeline ─────────────────────────────────────────────────
        codepipeline.CfnPipeline(
            self, "Pipeline",
            name="ec2-pipeline",
            role_arn=pipeline_role.role_arn,
            artifact_store=codepipeline.CfnPipeline.ArtifactStoreProperty(
                type="S3",
                location=artifact_bucket.bucket_name,
            ),
            restart_execution_on_update=False,
            stages=[
                codepipeline.CfnPipeline.StageDeclarationProperty(
                    name="Source",
                    actions=[
                        codepipeline.CfnPipeline.ActionDeclarationProperty(
                            name="GitHub_Source",
                            action_type_id=codepipeline.CfnPipeline.ActionTypeIdProperty(
                                category="Source",
                                owner="ThirdParty",
                                provider="GitHub",
                                version="1",
                            ),
                            output_artifacts=[
                                codepipeline.CfnPipeline.OutputArtifactProperty(name="SourceOutput")
                            ],
                            configuration={
                                "Owner": "Abdelali12-codes",
                                "Repo": "aws-codedeploy-sample",
                                "Branch": "master",
                                "OAuthToken": cdk.SecretValue.secrets_manager("github-access-token").unsafe_unwrap(),
                                "PollForSourceChanges": False,
                            },
                            run_order=1,
                        )
                    ],
                ),
                codepipeline.CfnPipeline.StageDeclarationProperty(
                    name="Build",
                    actions=[
                        codepipeline.CfnPipeline.ActionDeclarationProperty(
                            name="Build",
                            action_type_id=codepipeline.CfnPipeline.ActionTypeIdProperty(
                                category="Build",
                                owner="AWS",
                                provider="CodeBuild",
                                version="1",
                            ),
                            input_artifacts=[
                                codepipeline.CfnPipeline.InputArtifactProperty(name="SourceOutput")
                            ],
                            output_artifacts=[
                                codepipeline.CfnPipeline.OutputArtifactProperty(name="BuildOutput")
                            ],
                            configuration={"ProjectName": build_project.name},
                            run_order=1,
                        )
                    ],
                ),
                codepipeline.CfnPipeline.StageDeclarationProperty(
                    name="Deploy",
                    actions=[
                        codepipeline.CfnPipeline.ActionDeclarationProperty(
                            name="Deploy",
                            action_type_id=codepipeline.CfnPipeline.ActionTypeIdProperty(
                                category="Deploy",
                                owner="AWS",
                                provider="CodeDeploy",
                                version="1",
                            ),
                            input_artifacts=[
                                codepipeline.CfnPipeline.InputArtifactProperty(name="BuildOutput")
                            ],
                            configuration={
                                "ApplicationName": application.application_name,
                                "DeploymentGroupName": deployment_group.deployment_group_name,
                            },
                            run_order=1,
                        )
                    ],
                ),
            ],
        )

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
                # SSM: allows Session Manager access and agent communication
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                # S3 read: CodeDeploy agent downloads the revision bundle from S3
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"),
            ],
        )

        # ── VPC + Ubuntu EC2 instance ───────────────────────────────────
        vpc_cidr = self.node.try_get_context("vpc_cidr") or "10.10.0.0/16"
        vpc = ec2.Vpc(self, "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(vpc_cidr),
            max_azs=1,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )

        sg = ec2.SecurityGroup(self, "Sg", vpc=vpc)
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(8080))
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22))
        # CodeDeploy agent calls the CodeDeploy service endpoint over HTTPS
        # The instance needs outbound 443 — allowed by default on SG egress,
        # but the VPC needs a route to the internet (NAT or IGW).
        # For a public subnet with IGW, add a public IP to the instance.
        sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443))
        sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -e",
            "exec > /var/log/user-data.log 2>&1",  # log everything for debugging
            "export DEBIAN_FRONTEND=noninteractive",

            # Wait for apt lock to be released (cloud-init may hold it on boot)
            "while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do sleep 2; done",

            "apt-get update -y",
            "apt-get install -y ruby-full wget python3 python3-pip python3-venv iproute2 curl",

            # Install CodeDeploy agent
            # The install script is region-specific — must match the instance region
            "REGION=$(curl -sf http://169.254.169.254/latest/meta-data/placement/region)",
            "wget -O /tmp/codedeploy-install https://aws-codedeploy-${REGION}.s3.${REGION}.amazonaws.com/latest/install",
            "chmod +x /tmp/codedeploy-install",
            "/tmp/codedeploy-install auto",

            # Ensure agent is running and enabled on reboot
            "systemctl enable codedeploy-agent",
            "systemctl start codedeploy-agent",

            # Verify agent is running — fail user_data loudly if not
            "systemctl is-active --quiet codedeploy-agent || { echo 'ERROR: codedeploy-agent failed to start'; exit 1; }",

            # Pre-create app directories
            "mkdir -p /var/www/my-app",
            "mkdir -p /var/run/my-app",
        )

        instance = ec2.Instance(
            self, "AppInstance",
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO),
            machine_image=ec2.MachineImage.from_ssm_parameter(
                "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            # Public IP required so the instance can reach CodeDeploy + S3
            # endpoints without a NAT gateway
            associate_public_ip_address=True,
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
        # Required so the webhook can start a pipeline execution
        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=["codepipeline:StartPipelineExecution"],
            resources=["*"],
        ))

        # ── CfnPipeline ─────────────────────────────────────────────────
        pipeline = codepipeline.CfnPipeline(
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

        # ── CfnWebhook ───────────────────────────────────────────────
        # A webhook registers a URL with GitHub so that on every push to
        # the configured branch, GitHub sends an HTTP POST to CodePipeline.
        # CodePipeline verifies the request using a secret token (stored in
        # Secrets Manager) and starts a pipeline execution immediately.
        #
        # PollForSourceChanges: False in the Source action is REQUIRED when
        # using a webhook — otherwise CodePipeline polls every minute AND
        # the webhook triggers it, causing duplicate executions.
        #
        # authentication: GITHUB_HMAC
        #   GitHub signs every webhook payload with HMAC-SHA1 using the
        #   secret token. CodePipeline verifies the signature before
        #   starting the pipeline — prevents unauthorized triggers.
        #
        # filters: push events on the target branch only.
        #   JsonPath  → selects the field from the GitHub webhook payload
        #   MatchEquals → value that field must equal to trigger the pipeline
        webhook_secret = cdk.SecretValue.secrets_manager("github-access-token")

        codepipeline.CfnWebhook(
            self, "PipelineWebhook",
            name="ec2-pipeline-webhook",
            # GITHUB_HMAC: GitHub signs the payload; CodePipeline verifies
            # the signature using the shared secret before accepting the event
            authentication="GITHUB_HMAC",
            authentication_configuration=codepipeline.CfnWebhook.WebhookAuthConfigurationProperty(
                secret_token=webhook_secret.unsafe_unwrap(),
            ),
            # Which pipeline and stage/action to trigger
            target_pipeline=pipeline.ref,
            target_pipeline_version=1,
            target_action="GitHub_Source",
            # Register the webhook URL with GitHub automatically
            register_with_third_party=True,
            filters=[
                # Trigger only on push events to the target branch
                codepipeline.CfnWebhook.WebhookFilterRuleProperty(
                    # $.ref is the GitHub push event field containing the branch
                    # e.g. "refs/heads/master"
                    json_path="$.ref",
                    match_equals="refs/heads/master",
                ),
            ],
        )

        cdk.CfnOutput(self, "WebhookUrl", value="See AWS Console → CodePipeline → ec2-pipeline → Settings → Webhook")

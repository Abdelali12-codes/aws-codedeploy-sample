#!/usr/bin/env python3
import aws_cdk as cdk
from stack import CodeDeployEC2Stack

app = cdk.App()
CodeDeployEC2Stack(app, "CodeDeployEC2Stack")
app.synth()

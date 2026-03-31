#!/bin/bash
# AfterInstall — runs AFTER CodeDeploy copies files
# Goal: install dependencies, set permissions, configure app
set -e
cd /var/www/my-app
pip install -r requirements.txt
chown -R ubuntu:ubuntu /var/www/my-app

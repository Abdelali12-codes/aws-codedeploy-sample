#!/bin/bash
# BeforeInstall — runs BEFORE CodeDeploy copies files
# Goal: clean old files, create directories, take backups
set -e
rm -rf /var/www/my-app
mkdir -p /var/www/my-app

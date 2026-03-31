#!/bin/bash
# BeforeInstall — runs BEFORE CodeDeploy copies files
# Goal: clean old files, create directories, take backups
set -e
sudo apt install python3 python3-pip -y
rm -rf /var/www/my-app
mkdir -p /var/www/my-app

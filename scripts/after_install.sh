#!/bin/bash
# AfterInstall — runs AFTER CodeDeploy copies files
# Goal: install dependencies, set permissions, configure app
set -e
cd /var/www/my-app

# Python 3.12+ (Debian/Ubuntu) enforces PEP 668 and blocks system-wide pip.
# Use a virtualenv instead.
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

chown -R ubuntu:ubuntu /var/www/my-app

# Create the systemd unit file so ApplicationStart can use systemctl
cat > /etc/systemd/system/my-app.service << 'EOF'
[Unit]
Description=My App
After=network.target

[Service]
WorkingDirectory=/var/www/my-app
ExecStart=/var/www/my-app/.venv/bin/python main.py
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable my-app

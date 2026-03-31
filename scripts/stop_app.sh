#!/bin/bash
# ApplicationStop — runs against the OLD version still on the instance
# Goal: gracefully stop the running application
set -e
systemctl stop my-app || true   # 'true' prevents failure if app isn't running

# 02 — CodeDeploy EC2 + AppSpec Hooks (CDK Python)

## What is CodeDeploy?
AWS CodeDeploy automates application deployments to EC2, on-premises servers, Lambda, and ECS. It handles the deployment lifecycle so you don't have to write custom deployment scripts.

---

## appspec.yml — The Deployment Blueprint
The `appspec.yml` file **must be at the root** of your deployment artifact. CodeDeploy reads it to know:
1. Which files to copy and where (`files` section)
2. Which scripts to run and when (`hooks` section)

---

## EC2 Deployment Lifecycle — Full Hook Order

```
┌─────────────────────────────────────────────────────────┐
│                  IN-PLACE DEPLOYMENT                    │
├──────────────────────┬──────────────────────────────────┤
│ Hook                 │ Purpose                          │
├──────────────────────┼──────────────────────────────────┤
│ ApplicationStop      │ Stop the OLD running app         │
│ DownloadBundle       │ CodeDeploy downloads artifact    │
│ BeforeInstall        │ Pre-copy tasks (clean dirs)      │
│ Install              │ CodeDeploy copies files          │
│ AfterInstall         │ Post-copy (deps, permissions)    │
│ ApplicationStart     │ Start the NEW app                │
│ ValidateService      │ Smoke test — is the app healthy? │
└──────────────────────┴──────────────────────────────────┘
```

> **ELB-only hooks** (only available when a load balancer is attached):
> `BeforeBlockTraffic` → `BlockTraffic` → `AfterBlockTraffic`
> `BeforeAllowTraffic` → `AllowTraffic` → `AfterAllowTraffic`
> See `05-codedeploy-elb-integration` for details.

### Key Rules
- Scripts exit **non-zero** → deployment **fails** → auto rollback triggers
- `timeout` default is **3600 seconds** (max is also 3600)
- `runas` sets which OS user runs the script
- `ApplicationStop` runs the script from the **previous** (currently installed) revision
- If no previous revision exists, `ApplicationStop` is **skipped**

---

## Deployment Configurations (Traffic shifting strategies)

| Config | Behavior |
|---|---|
| `ONE_AT_A_TIME` | Deploy to 1 instance at a time |
| `HALF_AT_A_TIME` | Deploy to 50% of instances at a time |
| `ALL_AT_ONCE` | Deploy to all instances simultaneously (fastest, riskiest) |
| Custom | Define your own minimum healthy hosts |

---

## In-Place vs Blue/Green

| | In-Place | Blue/Green |
|---|---|---|
| How | Stop → deploy → start on same instances | Launch new instances → shift traffic → terminate old |
| Downtime | Brief (during restart) | Zero downtime |
| Rollback | Re-deploy previous revision | Re-route traffic to old instances |
| Cost | No extra instances | Temporary double capacity |

---

## Artifact Structure (what goes in the zip)
```
my-app.zip
├── appspec.yml          ← MUST be at root
├── scripts/
│   ├── stop_app.sh
│   ├── before_install.sh
│   ├── after_install.sh
│   ├── start_app.sh
│   └── validate.sh
├── app/
│   └── ... application files ...
└── config/
    └── nginx.conf
```

---

## CodeDeploy Agent
The **CodeDeploy agent** must be installed and running on every EC2 instance. It:
- Polls CodeDeploy for pending deployments
- Downloads the artifact from S3
- Executes the appspec hooks
- Reports status back to CodeDeploy

Install via user data (see `stack.py`) or SSM Run Command.

---

## Exam Tips
- `appspec.yml` must be at the **root** of the artifact — not in a subdirectory
- `ValidateService` is your last chance to catch a bad deployment before it's considered successful
- `ApplicationStop` uses the **old revision's** appspec, not the new one
- CodeDeploy needs an **IAM instance profile** on EC2 to pull from S3
- Tags on EC2 instances are how CodeDeploy identifies deployment targets
- `auto_rollback` on failed deployment is a best practice — always enable it

---

## Deploy
```bash
pip install -r requirements.txt
cdk bootstrap
cdk deploy
```

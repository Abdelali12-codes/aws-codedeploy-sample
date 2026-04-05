# hotfix_patch.py
# This file was originally uploaded manually to instances as an emergency fix.
# ROOT CAUSE of the outage: it was never part of any revision, so it was
# removed when CodeDeploy rolled back to the previous revision.
#
# PERMANENT FIX: hotfix files are now always included in the revision inside
# app/ so they are deployed alongside main.py with no path gymnastics.
# appspec.yml deploys app/ → /var/www/my-app with file_exists_behavior: RETAIN
# on the hotfix entry so any instance-specific edits survive deployments.

def apply_quiz_score_fix(score: int) -> int:
    """
    Hotfix: quiz scores were being calculated with an off-by-one error.
    Scores above 100 were possible due to a boundary condition bug.
    """
    return min(score, 100)

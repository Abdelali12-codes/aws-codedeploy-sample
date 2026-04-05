# hotfix_patch.py
# This file was originally uploaded manually to instances as an emergency fix.
# ROOT CAUSE of the outage: it was never part of any revision, so it was
# removed when CodeDeploy rolled back to the previous revision.
#
# PERMANENT FIX: hotfix files are now always included in the revision.
# appspec.yml deploys this with file_exists_behavior: RETAIN so any
# instance-specific edits made after deployment are preserved.

def apply_quiz_score_fix(score: int) -> int:
    """
    Hotfix: quiz scores were being calculated with an off-by-one error.
    Scores above 100 were possible due to a boundary condition bug.
    """
    return min(score, 100)

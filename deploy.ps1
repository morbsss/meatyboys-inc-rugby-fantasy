# -----------------------------------------------------------------------------
# deploy.ps1 - RETIRED. Deploys now run in CI.
#
# This script used to copy the working tree straight from a Windows machine to
# the VM. That meant whatever was on someone's disk at the time - uncommitted
# edits, a stale branch, an unreviewed experiment - became production, with no
# record of what shipped and no tests in the way.
#
# Deployment now happens in .github/workflows/deploy.yml when main moves, so the
# VM always matches a commit that exists on main and passed its tests.
#
#   To deploy      : merge your branch into main.
#   To re-deploy   : GitHub → Actions → "CI / Deploy" → Run workflow.
#   To check state : GitHub → Actions (each run logs what it copied and its
#                    post-deploy smoke test).
#
# The VM refuses manual deploys too - deploy.sh exits unless CI_DEPLOY=1. See
# the break-glass note there for restoring service if CI itself is down.
# -----------------------------------------------------------------------------

Write-Host ""
Write-Host "  Local deploys are disabled." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Deployment runs in CI when main moves:"
Write-Host "    .github/workflows/deploy.yml"
Write-Host ""
Write-Host "  To ship:      merge your branch into main"
Write-Host "  To re-deploy: GitHub -> Actions -> 'CI / Deploy' -> Run workflow"
Write-Host ""
Write-Host "  Why: a local deploy pushes your working tree, not a reviewed" -ForegroundColor DarkGray
Write-Host "  commit, and leaves no record of what shipped." -ForegroundColor DarkGray
Write-Host ""

exit 1

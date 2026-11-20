# Run once on the Windows PC inside this repository.
# Sets a repo-local noreply identity so real email never enters commit metadata,
# and enables the checked-in pre-push hook that blocks non-noreply commits.
$ErrorActionPreference = "Stop"
git config user.name "asarimaker"
git config user.email "253086933+asarimaker@users.noreply.github.com"
git config core.hooksPath ".githooks"
Write-Host "Git identity for this repo:"
git config user.name
git config user.email
Write-Host "core.hooksPath:"
git config core.hooksPath
Write-Host "Done. Also enable on github.com/settings/emails:"
Write-Host "  - Keep my email addresses private"
Write-Host "  - Block command line pushes that expose my email"

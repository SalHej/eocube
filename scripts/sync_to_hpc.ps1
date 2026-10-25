# Push local commits, then pull them on the cluster.
# Code travels via GitHub. Data never leaves the cluster.
param(
    [string]$Message = "",
    [string]$Host    = "",      # set after FreeIPA setup
    [string]$User    = ""
)
$ErrorActionPreference = "Stop"
$PROJECT = "/scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828"

Write-Host "Running tests locally..." -ForegroundColor Cyan
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed. Not syncing." }

if ($Message) {
    git add -A
    git commit -m $Message
}
git push

if (-not $Host -or -not $User) {
    Write-Host "Pushed to GitHub. Set -Host and -User to pull on the cluster." -ForegroundColor Yellow
    exit 0
}
Write-Host "Pulling on cluster..." -ForegroundColor Cyan
ssh "$User@$Host" "cd $PROJECT/eocube && git pull --ff-only && python -m pytest -q"

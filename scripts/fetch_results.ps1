# Bring back results only: JSON, CSV, logs. Never imagery.
param(
    [Parameter(Mandatory=$true)][string]$Host,
    [Parameter(Mandatory=$true)][string]$User
)
$PROJECT = "/scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828"
New-Item -ItemType Directory -Force -Path .\results | Out-Null

# include-list, not exclude-list: licensed rasters must not be copied by accident
scp "${User}@${Host}:$PROJECT/eocube/results/*.json" .\results\
scp "${User}@${Host}:$PROJECT/eocube/results/*.csv"  .\results\ 2>$null
Write-Host "Results fetched. Imagery was not transferred." -ForegroundColor Green

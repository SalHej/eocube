#!/bin/bash
#SBATCH --job-name=eocube-fetch
#SBATCH --output=%x-%j.out
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --partition=main
# Submit from ~/work/logs, passing the account and the year:
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/fetch_year.sbatch 2019
#
# Fetches one year of the open cube (PLAN.md, Step 4) into ~/work/data/raw/open_cube/<year>:
# S1-RTC and S2-L2A chips at every usable Twente station plus the DANS in-situ files.
# Network-bound: 8 threads read 8 remote files at a time. Resumable: re-submitting
# skips chips already on disk, so a timeout loses nothing.

set -euo pipefail
YEAR=${1:-2019}

source "$HOME/eocube/scripts/stage_env.sh"
echo "node: $(hostname)   year: $YEAR   started: $(date -Is)"
python "$HOME/eocube/experiments/open_cube/fetch_year.py" \
    --year "$YEAR" --out "$HOME/work/data/raw/open_cube" --workers "$SLURM_CPUS_PER_TASK"
du -sh "$HOME/work/data/raw/open_cube/$YEAR"
echo "finished: $(date -Is)"

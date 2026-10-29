#!/bin/bash
#SBATCH --job-name=eocube-cube
#SBATCH --output=%x-%j.out
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --partition=main
# Submit from ~/work/logs, passing the account and the year:
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/build_cube.sbatch 2019
#
# Assembles the open cube from ~/work/data/raw/open_cube/<year> (fetch_year.py output)
# into ~/work/data/cubes/open_cube/<year> and runs the five gates on every sample
# (experiments/open_cube/build_cube.py). Reads only local Lustre files; no network.

set -euo pipefail
YEAR=${1:-2019}

source "$HOME/eocube/scripts/stage_env.sh"
echo "node: $(hostname)   year: $YEAR   started: $(date -Is)"
python "$HOME/eocube/experiments/open_cube/build_cube.py" \
    --raw "$HOME/work/data/raw/open_cube/$YEAR" --out "$HOME/work/data/cubes/open_cube/$YEAR"
echo "finished: $(date -Is)"

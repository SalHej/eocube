#!/bin/bash
#SBATCH --job-name=eocube-window
#SBATCH --output=%x-%j.out
#SBATCH --time=00:45:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --partition=main
# Submit from ~/work/logs, passing the account and the year:
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/window_size.sbatch 2019
#
# Window-size experiment (experiments/open_cube/window_size.py; docs/window_size.md):
# extract 9 x 9 windows from the fetched chips, then evaluate windows 1, 3, 5, 9.
# Reads local chips and the built cube only; no network. CPU only.

set -euo pipefail
YEAR=${1:-2019}
D=$HOME/work/data
X=$HOME/eocube/experiments/open_cube

source "$HOME/eocube/scripts/stage_env.sh"
echo "node: $(hostname)   year: $YEAR   started: $(date -Is)"
python "$X/window_size.py" extract --raw "$D/raw/open_cube/$YEAR" --cube "$D/cubes/open_cube/$YEAR" --year "$YEAR"
python "$X/window_size.py" evaluate --cube "$D/cubes/open_cube/$YEAR" \
    --out "$HOME/work/results/open_cube/window_$YEAR"
echo "finished: $(date -Is)"

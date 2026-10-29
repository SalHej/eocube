#!/bin/bash
#SBATCH --job-name=eocube-ablation
#SBATCH --output=%x-%j.out
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --partition=main
# Submit from ~/work/logs, passing the account, the year and the number of seeds:
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/ablation.sbatch 2019 5
#
# Leave-one-gate-out ablation on a built cube (experiments/open_cube/run_ablation.py).
# Reads samples.csv and gates.json only; no imagery, no network. CPU only.

set -euo pipefail
YEAR=${1:-2019}
SEEDS=${2:-5}

source "$HOME/eocube/scripts/stage_env.sh"
echo "node: $(hostname)   year: $YEAR   seeds: $SEEDS   started: $(date -Is)"
python "$HOME/eocube/experiments/open_cube/run_ablation.py" \
    --cube "$HOME/work/data/cubes/open_cube/$YEAR" \
    --out "$HOME/work/results/open_cube/ablation_$YEAR" --seeds "$SEEDS"
echo "finished: $(date -Is)"

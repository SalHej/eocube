#!/bin/bash
#SBATCH --job-name=eocube-general
#SBATCH --output=%x-%j.out
#SBATCH --time=00:45:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --partition=main
# Submit from ~/work/logs, passing the account, the stage(s) and the year:
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/generalisation.sbatch "aux arm-a" 2019
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/generalisation.sbatch arm-b 2019
#
# Generalisation experiment (experiments/open_cube/generalisation.py; docs/generalisation.md).
# aux reads SoilGrids and NASADEM at the 17 station points (network, a few MB); arm-a and
# arm-b read the built cube only. CPU only.

set -euo pipefail
STAGES=${1:?stages, e.g. "aux arm-a"}
YEAR=${2:-2019}
CUBE=$HOME/work/data/cubes/open_cube/$YEAR
OUT=$HOME/work/results/open_cube/generalisation_$YEAR

source "$HOME/eocube/scripts/stage_env.sh"
echo "node: $(hostname)   stages: $STAGES   started: $(date -Is)"
for s in $STAGES; do
    python "$HOME/eocube/experiments/open_cube/generalisation.py" "$s" --cube "$CUBE" --out "$OUT"
done
echo "finished: $(date -Is)"

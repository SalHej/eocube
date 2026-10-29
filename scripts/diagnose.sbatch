#!/bin/bash
#SBATCH --job-name=eocube-diagnose
#SBATCH --output=%x-%j.out
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --partition=main
# Submit from ~/work/logs, passing the account and the diagnostic:
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/diagnose.sbatch skill
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/diagnose.sbatch bypass-fetch
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/diagnose.sbatch bypass-assemble
#
# Diagnostics for docs/open_ablation.md, not results: diagnose_skill.py (reads samples.csv
# only) and bypass_cube.py (bypass-fetch reads the chips the fetcher's protections
# replaced or withheld, from the Planetary Computer; bypass-assemble is local only).

set -euo pipefail
WHAT=${1:?skill, bypass-fetch or bypass-assemble}
YEAR=${2:-2019}
D=$HOME/work/data
X=$HOME/eocube/experiments/open_cube

source "$HOME/eocube/scripts/stage_env.sh"
echo "node: $(hostname)   $WHAT $YEAR   started: $(date -Is)"
case "$WHAT" in
  skill)           python "$X/diagnose_skill.py" --cube "$D/cubes/open_cube/$YEAR" ;;
  bypass-fetch)    python "$X/bypass_cube.py" fetch --raw "$D/raw/open_cube/$YEAR" \
                       --bypass "$D/raw/open_cube_bypass/$YEAR" --year "$YEAR" ;;
  bypass-assemble) python "$X/bypass_cube.py" assemble --raw "$D/raw/open_cube/$YEAR" \
                       --bypass "$D/raw/open_cube_bypass/$YEAR" --year "$YEAR" \
                       --out "$HOME/work/results/open_cube/bypass_$YEAR" ;;
esac
echo "finished: $(date -Is)"

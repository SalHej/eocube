#!/bin/bash
# Run once on the cluster login node, after FreeIPA setup and allocation access.
set -euo pipefail

PROJECT=/scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828

echo "== what is available =="
sinfo -o "%P %G %D %m %l" || true
echo
module avail 2>&1 | head -40 || true
echo
df -h "$PROJECT" || true

mkdir -p "$PROJECT/venvs" "$PROJECT/data" "$PROJECT/eocube"
cd "$PROJECT"

if [ ! -d eocube/.git ]; then
    git clone https://github.com/SalHej/eocube.git eocube
fi

# module load python/3.11     # adjust to what `module avail` showed
python -m venv venvs/eocube
source venvs/eocube/bin/activate
pip install --upgrade pip
pip install -e "eocube[dev,ml,raster]"

cd eocube && python -m pytest -q
echo "Bootstrap complete. Submit with: sbatch scripts/train.sbatch"

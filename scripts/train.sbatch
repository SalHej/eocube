#!/bin/bash
#SBATCH --job-name=eocube-ablation
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
# --- uncomment for GPU runs; GPU hours are scarce (1000 total) ---
# #SBATCH --gres=gpu:1
# #SBATCH --partition=gpu

set -euo pipefail

PROJECT=/scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828
REPO=$PROJECT/eocube
VENV=$PROJECT/venvs/eocube

mkdir -p "$REPO/logs" "$REPO/results"
cd "$REPO"

# module load python/3.11      # adjust after checking `module avail`
source "$VENV/bin/activate"

echo "host:    $(hostname)"
echo "job:     $SLURM_JOB_ID"
echo "started: $(date -Is)"
python -c "import sys; print('python:', sys.version.split()[0])"
nvidia-smi 2>/dev/null || echo "no GPU on this node"

python -m pytest -q

python experiments/run_ablation.py \
    --config experiments/configs/"${CONFIG:-peatland.yaml}" \
    --out results/ablation-"$SLURM_JOB_ID".json \
    --seeds 5

echo "finished: $(date -Is)"

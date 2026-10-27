#!/bin/bash
#SBATCH --job-name=eocube-setup
#SBATCH --output=%x-%j.out
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --partition=main
# Submit from ~/work/logs so the log lands there, and pass the account explicitly:
#   cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/setup_env.sbatch
#
# Builds the Python environment (dev, ml, raster extras; no torch) on a compute node
# and runs the test suite there, never on the login node.
#
# A venv is thousands of small files, which Lustre handles badly. So the venv is built
# in node-local /tmp and packed into ONE file, ~/envs/eocube.tar; each job unpacks it
# to the same /tmp path (a venv only works at the path it was built at) via
# scripts/env_activate.sh. If the node has no usable local /tmp, this falls back to a
# plain venv on Lustre at ~/envs/eocube and says so.
#
# Also records what the fetch job depends on: outbound internet from compute nodes.

set -euo pipefail

REPO=$HOME/eocube
LOCAL_VENV=/tmp/$USER/eocube-venv
PACKED=$HOME/envs/eocube.tar
LUSTRE_VENV=$HOME/envs/eocube
MIN_TMP_KB=$((3 * 1024 * 1024))          # 3 GB free needed to build and unpack

echo "node: $(hostname)   started: $(date -Is)"

echo "== outbound internet from the compute node"
for url in https://planetarycomputer.microsoft.com/api/stac/v1 \
           https://phys-techsciences.datastations.nl/api/info/version \
           https://pypi.org/simple/rasterio/; do
    code=$(curl -s -m 20 -o /dev/null -w '%{http_code}' "$url" || true)
    echo "  $code  $url"
done

echo "== node-local /tmp"
df -h /tmp | tail -1
fstype=$(findmnt -n -o FSTYPE --target /tmp || echo unknown)
avail_kb=$(df -Pk /tmp | awk 'NR==2 {print $4}')
echo "  filesystem type: $fstype   available: $((avail_kb / 1024)) MB"
case "$fstype" in
    lustre|nfs|nfs4|beegfs|gpfs) tmp_ok=no ;;
    *) [ "$avail_kb" -ge "$MIN_TMP_KB" ] && tmp_ok=yes || tmp_ok=no ;;
esac

if [ "$tmp_ok" = yes ]; then
    echo "== building packed venv in $LOCAL_VENV"
    VENV=$LOCAL_VENV
    rm -rf "$VENV"
else
    echo "== /tmp is not usable node-local storage; FALLBACK to plain venv on Lustre: $LUSTRE_VENV"
    VENV=$LUSTRE_VENV
fi
mkdir -p "$(dirname "$VENV")"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
source "$VENV/bin/activate"
python -m pip install -q --upgrade pip
python -m pip install -q -e "$REPO[dev,ml,raster]"
python - <<'EOF'
import numpy, rasterio, sklearn, sys
print(f"python {sys.version.split()[0]}  numpy {numpy.__version__}  "
      f"rasterio {rasterio.__version__} (GDAL {rasterio.__gdal_version__})  sklearn {sklearn.__version__}")
EOF
echo "venv files: $(find "$VENV" -type f | wc -l)   size: $(du -sh "$VENV" | cut -f1)"

echo "== tests"
cd "$REPO"
python -m pytest -q -p no:cacheprovider

if [ "$VENV" = "$LOCAL_VENV" ]; then
    echo "== packing to $PACKED"
    mkdir -p "$(dirname "$PACKED")"
    tar -C "$(dirname "$LOCAL_VENV")" -cf "$PACKED.partial" "$(basename "$LOCAL_VENV")"
    mv "$PACKED.partial" "$PACKED"
    ls -l "$PACKED"
    rm -rf "$LOCAL_VENV"
fi

echo "finished: $(date -Is)"

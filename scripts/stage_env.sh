# Source at the top of a Slurm job:   source ~/eocube/scripts/stage_env.sh
#
# Unpacks the environment built by setup_env.sbatch (~/envs/eocube.tar) into
# node-local /tmp, at the same path it was built at, and activates it. Unpacks
# only if this node does not already hold an up-to-date copy. Falls back to the
# plain Lustre venv (~/envs/eocube) if setup_env.sbatch had to use one.

_local=/tmp/$USER/eocube-venv
_packed=$HOME/envs/eocube.tar

if [ -f "$_packed" ]; then
    if [ ! -x "$_local/bin/python" ] || [ "$_packed" -nt "$_local/.unpacked" ]; then
        rm -rf "$_local"
        mkdir -p "$(dirname "$_local")"
        tar -C "$(dirname "$_local")" -xf "$_packed"
        touch "$_local/.unpacked"
    fi
    source "$_local/bin/activate"
elif [ -x "$HOME/envs/eocube/bin/python" ]; then
    source "$HOME/envs/eocube/bin/activate"
else
    echo "stage_env.sh: no environment found; run scripts/setup_env.sbatch first" >&2
    return 1 2>/dev/null || exit 1
fi
unset _local _packed

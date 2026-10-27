# VU HPC setup

Status: in progress, started 2026-10-27. Sections marked **pending** are filled in
once the step that produces them has run.

## Access

```
laptop ──ssh──▶ uosis.mif.vu.lt ──ssh──▶ hpc (login3)
```

`~/.ssh/config` on the laptop (ed25519 key, no password prompt):

```
Host vu-uosis
    HostName uosis.mif.vu.lt
    User mif76448
    IdentityFile ~/.ssh/id_ed25519

Host vu-hpc
    HostName hpc
    User mif76448
    ProxyJump vu-uosis
    IdentityFile ~/.ssh/id_ed25519
```

- `ssh vu-hpc hostname` prints `login3`, with both Windows OpenSSH 9.5 and Git's
  OpenSSH 10.3.
- `dashboard.json` has `hpc.host = "vu-hpc"` and `hpc.user = "mif76448"`.
  `python -m labdash.cli status` reports `cluster connected`.

**login3 is for editing, git and job submission only.** The login banner forbids
calculations there. Tests, installs that compile anything, and downloads run in Slurm
jobs.

## Paths

| What | Path |
|---|---|
| Home (Lustre) | `/scratch/lustre/home/mif76448` |
| Code (git clone of github.com/SalHej/eocube) | `~/eocube` |
| Untouched downloads | `~/work/data/raw` |
| Assembled, contract-checked cubes | `~/work/data/cubes` |
| Ablation JSON, metrics | `~/work/results` |
| Trained weights | `~/work/models` |
| Slurm output | `~/work/logs` |
| Temporary, deletable | `~/work/scratch` |

Code and data are kept apart, so git operations in `~/eocube` can never touch results.

## Quota (2026-10-27)

| Command | Result |
|---|---|
| `lfs quota -h -u mif76448 /scratch/lustre` | used 197 KB, 12 files; **block and inode quota and limit 0, i.e. none set** |
| `quota -s` | no output (no quota configured) |
| `df -h /scratch/lustre/home/mif76448` | 461 TB filesystem, 308 TB used, **154 TB available** |

The planned storage (about 2.1 GB for one year, about 3.5 GB for 2017–2020) is far
inside this. **Unknown:** whether `/scratch/lustre/home` has a purge policy for old
files. No quota does not mean files are kept indefinitely. Ask the administrator.

## Software available

| Item | Found |
|---|---|
| OS Python | `/usr/bin/python3`, 3.12.3; `venv` and `ensurepip` work |
| Modules (`module avail`) | `gcc/13.3.0`, `python/3.12.3` (spack; resolves to the same `/usr/bin/python3`). **No GDAL, PROJ, conda or mamba modules** |
| Containers | `singularity-ce 4.1.1`. Pulling images works; building them does not (no `/etc/subuid` entry, so no `--fakeroot`) |
| Node-local scratch | none visible from login3 (`TMPDIR` unset; no `/local`, `/localscratch`). Compute-node `/tmp` is **pending** a test job |

rasterio's PyPI wheels bundle their own GDAL and PROJ, so the missing GDAL module does
not block installation.

## Environment

**Decision (2026-10-27): a packed venv.** A venv is thousands of small files, and Lustre
handles small-file metadata badly, so the venv is kept on Lustre as **one file**.

- **Build:** `scripts/setup_env.sbatch` runs on a compute node. It creates the venv in
  node-local `/tmp/$USER/eocube-venv`, installs `eocube[dev,ml,raster]` (no torch,
  editable from `~/eocube`), runs the test suite, then packs the venv into
  **`~/envs/eocube.tar`**.
- **Use:** every job starts with `source ~/eocube/scripts/stage_env.sh`. That
  unpacks the tarball to the same `/tmp` path (a venv only works at the path it was
  built at), only if the node lacks an up-to-date copy, then activates it.
- **Fallback:** if a compute node's `/tmp` is a network filesystem or has under 3 GB
  free, the build job makes a plain venv on Lustre at `~/envs/eocube` and logs that
  it did. `stage_env.sh` then uses that instead.
- **Rejected:** a container. Building images needs `--fakeroot`, which this account
  lacks.

Build:
```bash
cd ~/work/logs && sbatch -A mif76448_mif ~/eocube/scripts/setup_env.sbatch
```

**First build: job 247519, node-25, 2026-10-27, 39 s elapsed, 2.6 CPU-minutes.**

| Item | Result |
|---|---|
| Compute-node internet | Planetary Computer, DANS and PyPI all return HTTP 200 |
| Node-local `/tmp` | `/dev/nvme0n1`, xfs, 1.5 TB free on node-25 (it varies by node; 195 GB reported elsewhere). Not Lustre |
| Packages | Python 3.12.3, numpy 2.5.4, rasterio 1.5.2 (bundled GDAL 3.12.2), scikit-learn 1.9.1, from manylinux wheels |
| venv | 8,365 files, 434 MB, packed to **one file**: `~/envs/eocube.tar` (443 MB) |
| Tests | **87 passed, 2 skipped** (the laptop has 101) |

The 2 skips are whole modules, `tests/test_experiment_configs.py` (5 tests) and
`tests/test_run_ablation.py` (8 tests), which `importorskip("yaml")`. **PyYAML is in no
eocube extra.** The laptop happened to have it, while this clean environment does not.
Until it is added (needs approval), YAML experiment configs cannot be loaded on the
cluster, and those 13 tests do not run here.

Python 3.12 also warns that `datetime.utcnow()` in `eocube/io.py` is deprecated.
It is harmless for now.

Activate in any job:
```bash
source ~/eocube/scripts/stage_env.sh      # unpacks to /tmp/$USER/eocube-venv if needed
```

## Job templates

| Script | Purpose | Resources |
|---|---|---|
| `scripts/setup_env.sbatch` | Build the packed venv; run the tests | `main`, 4 CPU, 8 GB, 30 min |
| `scripts/fetch_year.sbatch <year>` | One year of the open cube into `~/work/data/raw/open_cube/<year>` (resumable) | `main`, 8 CPU, 16 GB, 4 h |
| `scripts/train.sbatch` | Older template from v0.3; still points at the project directory, which is not accessible | — |

Submit from `~/work/logs` so the logs land there, and pass the account:
`sbatch -A mif76448_mif <script>`.

## Slurm

| Item | Found |
|---|---|
| Partitions | `main` (30 nodes, 48 CPU, 385 GB; default time 2 h, no maximum, 7.3 GB/CPU default memory), `main2` (5 nodes), `gpu` (3 nodes, 8 × V100 32 GB); `power`, `test2` drained |
| Account | **`mif76448_mif`** (personal), QOS `normal`, no limits shown |
| Project allocation | **`paraiska2025` is not associated with this user.** Jobs cannot be charged to it, so they run on the personal account |

## Still blocked

1. **Project directory** `/scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828`:
   permission denied, because the account is not in the project group. For the
   administrator, recorded 2026-10-27 on login3:
   ```
   $ id
   uid=2467(mif76448) gid=2467(mif76448_) groups=2467(mif76448_),898(ii),956(darb),964(users)
   $ ls -la /scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828
   ls: cannot open directory '/scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828': Permission denied
   $ ls -ld /scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828
   drwxrws--- 16 root hpc_project_a1fb2511d81f42fba1f872930bb56828 25600 Oct 26 12:38 ...
   ```
   The directory is mode `2770`, owned by `root`, group
   `hpc_project_a1fb2511d81f42fba1f872930bb56828` (gid 1038560). **`mif76448` is not a
   member of gid 1038560.** The request to the administrator: add `mif76448` to that
   group, and associate it with Slurm account `paraiska2025`.
2. **Allocation `paraiska2025`:** not associated with `mif76448`. Same root cause, most
   likely.
3. **Compute-node internet access:** login3 reaches Planetary Computer, DANS and PyPI
   (HTTP 200). Compute nodes are **pending** the test job. If they have no outbound
   access, the Step 4 fetch cannot run as a Slurm job.

## What was fetched

**Pending:** Step 4 (2019, all 17 stations, full year).

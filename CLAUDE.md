# eocube — working agreement

Contract-checked multi-source Earth Observation data cubes.
Preprint: doi:10.31223/X5TN59 · Archive: doi:10.5281/zenodo.22552042

## Hard rules — do not violate these

1. **Never copy licensed EO raster data to this machine.** TerraSAR-X and
   EnMAP are licensed by third parties and must remain on VU HPC, as must
   anything stored on the cluster. If a task seems to require downloading
   such imagery, stop and say so instead.
   *Exception:* openly published EO data (e.g. Sentinel-2 from Zenodo or
   Copernicus) may be downloaded for testing, into `data/` only. Rule 2
   still applies: it is never committed.
2. **Never commit data.** `.gitignore` excludes `data/`, `*.tif`, `*.nc`,
   `*.h5`. Do not add exceptions.
3. **Never commit credentials.** No SSH keys, no tokens, no `.env` files.
4. **Never force-push to `main`** without being asked explicitly.
5. **Do not run long jobs locally.** Training belongs on the cluster via Slurm.

## Where things run

| Task | Where |
|---|---|
| Editing code, tests, git | This laptop |
| Contract gates on metadata | Either |
| Training, ablation on real data | VU HPC, Slurm only |
| Storing rasters | `/scratch/lustre/projects/hpc_project_a1fb2511d81f42fba1f872930bb56828` |

Allocation `paraiska2025`: 6,000 CPU-h, 1,000 GPU-h, valid to 2026-11-30.
GPU hours are scarce and shared with the group — say what a job will cost
before submitting it.

## Project layout

```
eocube/contracts.py   five contract gates, no dependencies
eocube/io.py          raster-backed description (needs rasterio)
eocube/pipeline.py    sample extraction, spatial block CV
eocube/baselines.py   ridge / random forest / torch, one signature
eocube/ablation.py    leave-one-gate-out harness
tests/                41 tests, must stay green
examples/             runnable demonstrations
scripts/              HPC job templates and sync helpers
```

## Conventions

- Python 3.10+, line length 100, type hints on public functions.
- Every new gate needs: a function in `contracts.py`, registration in `GATES`
  and `run_all_gates`, and at least two tests (one pass, one refuse).
- `pytest -q` must pass before any commit. Run it; do not assume.
- Docstrings explain *why a check exists and what failure it prevents*, not
  just what the code does. Match the existing tone.
- Commit messages: imperative, one line, no emoji.

## Scientific rules that are easy to get wrong

- **Never use a random train/test split.** EO pixels are spatially
  autocorrelated; use `spatial_block_split`. This is not a style preference.
- **Scalers fit on training data only.** See `baselines.standardise`.
- **Minimum three seeds** for any reported result.
- **Report refusals as well as metric changes.** An over-rigid gate is a
  finding, not a failure.
- **Gap-filled pixels stay out of the test set** unless explicitly justified.

## When unsure

Ask. Do not invent thresholds, invent data, or fabricate results. If a number
is not available, say it is not available.


## Infrastructure operations

Use the `labdash` CLI for anything touching the cluster. Do not write raw
`ssh` or `scp` commands: the CLI validates job ids, quotes paths, and refuses
to transfer imagery.

```bash
labdash status                      # repo, cluster, allocation, results
labdash jobs                        # Slurm queue
labdash submit                      # sbatch scripts/train.sbatch
labdash tail 12345                  # follow a job's log
labdash cancel 12345
labdash sync -m "message"           # test, commit, push, pull on cluster
labdash fetch                       # results back (never imagery)
labdash publish -m "message"        # commit and push results
labdash pipeline -m "message"       # the whole loop, blocking
```

Add `--json` for parseable output.

Rules:

- `labdash sync` runs the tests first and aborts on failure. Do not pass
  `--skip-tests` unless asked.
- `labdash fetch` copies only .json, .csv, .txt, .log, .md, .out, .err. If a
  task seems to need imagery locally, stop and say so.
- `labdash cancel` is the only destructive remote operation. Confirm first.
- Before submitting a job, say what it will cost. The allocation is 1,000 GPU
  hours shared with the group.

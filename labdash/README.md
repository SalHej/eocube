# labdash

A monitoring dashboard for computational research projects.

**Standard library only.** No Flask, no npm, no CDN. Works offline, on a cluster
login node, or on a laptop with no internet. One JSON file configures it, so the
same tool serves every project you run.

```bash
python -m labdash                 # reads ./dashboard.json
python -m labdash -c other.json -p 9000
```

Opens `http://127.0.0.1:8787`.

## Panels

| Panel | Shows |
|---|---|
| **Repository** | branch, last commit, uncommitted files, ahead/behind, recent tags |
| **HPC connection** | live SSH check with latency, hostname, project disk usage |
| **Allocation** | CPU and GPU hours used against quota, with bars; expiry date |
| **Tests** | pass/fail counts and the tail of the last run |
| **Slurm jobs** | live queue plus seven days of history from `sacct` |
| **Results** | ablation JSON rendered as a sortable table and a delta chart |
| **Commands** | declared commands, local or over SSH, with full output |
| **Logs** | any file in the log directory, tailed |

Dark and light themes follow the system setting; the ◐ button overrides.

## Security

Three deliberate constraints, because this process holds SSH access to a
university cluster:

**The command panel runs a whitelist, not a shell.** Commands are declared in the
config and invoked by key. Nothing the browser sends reaches a shell. A web page
that can run arbitrary commands is a remote-code-execution hole, and typing
ad-hoc commands into a browser is not worth that. Use a terminal for that.

**It binds to 127.0.0.1.** Not reachable from the network. Do not change this
without putting authentication in front of it.

**No credentials are stored or prompted for.** SSH uses `BatchMode=yes` and your
existing keys. If key auth is not set up, the panel says so rather than asking
for a password it would then have to hold.

## Configuration

```json
{
  "project_name": "eocube",
  "repo_path": ".",
  "results_dir": "results",
  "log_dir": "logs",
  "refresh_seconds": 30,
  "hpc": {
    "host": "cluster.example.edu",
    "user": "yourlogin",
    "project_path": "/scratch/projects/yours",
    "allocation": {
      "name": "alloc2025", "cpu_hours": 6000, "gpu_hours": 1000,
      "expires": "2026-11-30", "used": {"cpu_hours": 0, "gpu_hours": 0}
    }
  },
  "commands": {
    "tests": {"label": "Run tests", "where": "local",
              "argv": ["python", "-m", "pytest", "-q"]},
    "queue": {"label": "Queue", "where": "remote", "remote": "squeue -u $USER"}
  }
}
```

`where` is `local` (uses `argv`, no shell) or `remote` (uses `remote`, sent over
SSH). Add or remove commands freely — that is the intended way to extend it.

Allocation **usage** figures are read from the config, not probed, because
quota reporting differs between sites. Update them from your portal, or leave
them at the ceiling and treat the bars as capacity rather than consumption.

## Results format

The Results panel renders any JSON containing a `rows` array with `arm`,
`mean_metric`, `stdev`, `delta_vs_baseline` and `refusal_rate` — the output of
`eocube.ablation.AblationResult.to_rows()`. Anything else is shown as formatted
JSON, so it is still useful for other projects.

## Reusing it elsewhere

Copy the `labdash/` directory and write a new `dashboard.json`. Nothing in the
code is specific to this project; the defaults simply happen to match it.

## Limitations

- Allocation usage is config-driven, not live.
- `sacct` and `squeue` output formats vary between sites; adjust the parsing in
  `adapters.py` if your cluster differs.
- Test status is cached for an hour. Use the Run tests button to force it.
- No authentication. Localhost only, by design.


## Command line

Everything the dashboard does is also a command, so an agent — Claude Code, a
cron job, CI — drives the same verbs you do instead of improvising SSH.

```bash
labdash status                      # repo, cluster, allocation, results
labdash jobs                        # Slurm queue
labdash submit                      # sbatch the training script
labdash tail 12345                  # follow a job's log
labdash cancel 12345
labdash sync -m "message"           # test, commit, push, pull on cluster
labdash fetch                       # results back; imagery never transfers
labdash publish -m "message"        # commit and push results
labdash pipeline -m "message"       # sync, submit, wait, fetch, publish
```

`--json` on any command gives parseable output.

### Why a fixed verb set

Giving an agent unrestricted shell access to a cluster holding licensed
third-party data is a different risk from giving it access to a git
repository: a mistaken `git` command is recoverable, a mistaken `rm` on a
Lustre filesystem is not. These verbs validate job ids against a numeric
pattern, quote every path, and copy back an allow-list of extensions. Job id
injection attempts (`12345; scancel 999`, `$(whoami)`) are rejected before
reaching a shell.

`labdash sync` runs the test suite first and aborts on failure — pushing code
that fails its own tests to a machine that will spend GPU hours on it is a
waste that is trivial to prevent.


## Assistant

A chat panel backed by the Anthropic Messages API with tool use. Model picker,
running cost estimate, and an approval gate.

### The approval gate

Tool calls are classified into three tiers before anything runs.

**AUTO** — read-only and reversible. Runs immediately: reading files, `git
status`, `labdash status`, `pytest`.

**REVIEW** — changes state. The exact command is shown and nothing happens
until you approve: writing files, committing, pushing, submitting jobs, and any
shell command not recognised as read-only.

**BLOCKED** — refused with no approval path. Recursive deletes at filesystem
roots, disk formatting, shutdown, SSH key or credential access, `git push
--force`, anything destructive against the cluster's Lustre path. The refusal
is returned to the model as a tool result, so it learns the boundary and
proposes something else rather than retrying.

The classifier is pattern-based and deliberately conservative: it will
sometimes send a harmless command to REVIEW. That is the correct direction to
fail in. An assistant that can run PowerShell on a machine holding cluster
credentials should not be able to do so unseen.

### Cost

Billed to your Anthropic API account, **separately from a Claude
subscription**. The panel shows a running estimate; treat it as indicative, as
rates change. Sonnet is the default because it handles this work at a fraction
of Opus cost.

See `SECRETS_SETUP.md` for key configuration.

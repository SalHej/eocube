# Setting up Claude Code for this project

## 1. Install (Windows)

```powershell
irm https://claude.ai/install.ps1 | iex
claude --version
claude doctor
```

Requires Git for Windows and a Claude Pro or Max plan (or API billing).
The free plan does not include Claude Code.

## 2. Add these files to your repo

```
CLAUDE.md                  <- project rules; Claude Code reads this automatically
scripts/bootstrap_hpc.sh   <- run once on the cluster
scripts/train.sbatch       <- Slurm job template
scripts/sync_to_hpc.ps1    <- test, commit, push, pull on cluster
scripts/fetch_results.ps1  <- bring back results only
```

Append `.gitignore_additions` to your existing `.gitignore`.

## 3. Start it

```powershell
cd <your repo>
claude
```

Claude Code reads `CLAUDE.md` on startup, so it knows the data rules, the
spatial-split requirement and the test conventions without being told each time.

## 4. Things worth asking it

- "Write tests/test_io.py covering the conflict-detection path"
- "Add an atmospheric-correction gate following the pattern in contracts.py"
- "_invalid_fraction only reads band 1 — fix it for multi-band rasters"
- "Band expressions match by substring, so B8 matches inside B80. Fix it."
- "Run the tests and tell me what broke"
- "Create the v0.3.0 release notes from CHANGELOG.md"

## 5. The HPC loop, once access works

On the cluster, once:
```bash
bash scripts/bootstrap_hpc.sh
```

Then from the laptop, each iteration:
```powershell
.\scripts\sync_to_hpc.ps1 -Message "add atmospheric gate" -Host <host> -User <user>
ssh <user>@<host> "cd /scratch/.../eocube && sbatch scripts/train.sbatch"
.\scripts\fetch_results.ps1 -Host <host> -User <user>
```

Code goes up through GitHub. Results come back as JSON. Rasters never move.

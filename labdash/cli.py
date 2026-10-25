"""
labdash.cli
===========

Command-line interface to every infrastructure operation in this project.

This exists so that an agent — Claude Code, a cron job, a CI runner — drives the
same well-defined verbs you do, rather than improvising raw SSH commands. An
improvised ``ssh ... rm -rf`` against a cluster holding licensed third-party
data is not recoverable with ``git checkout``; a fixed verb set is.

Every command accepts ``--json`` so output can be parsed rather than scraped.

    labdash status                       # repo, cluster, allocation
    labdash jobs                         # queue
    labdash submit                       # sbatch the training script
    labdash tail 12345                   # follow a job's log
    labdash cancel 12345
    labdash sync -m "add gate"           # test, commit, push, pull on cluster
    labdash fetch                        # bring back results (never imagery)
    labdash publish                      # commit and push results
    labdash pipeline -m "run ablation"   # the whole loop, blocking
    labdash serve                        # the dashboard
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import adapters, ops
from .server import load_config, serve


def _out(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj if isinstance(obj, (dict, list)) else obj.as_dict(),
                         indent=2, default=str))
    else:
        print(obj)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="labdash",
        description="Infrastructure operations for a computational research project.")
    ap.add_argument("-c", "--config", type=Path, default=Path("dashboard.json"))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="repository, cluster and allocation state")
    sub.add_parser("jobs", help="current Slurm queue")

    p = sub.add_parser("submit", help="submit the training job")
    p.add_argument("--script", default="scripts/train.sbatch")
    p.add_argument("--env", action="append", default=[],
                   metavar="K=V", help="environment variable for the job")

    p = sub.add_parser("tail", help="tail a job's log")
    p.add_argument("job_id")
    p.add_argument("-n", "--lines", type=int, default=120)

    p = sub.add_parser("cancel", help="cancel a job")
    p.add_argument("job_id")

    p = sub.add_parser("sync", help="test, commit, push, pull on cluster")
    p.add_argument("-m", "--message", default="")
    p.add_argument("--skip-tests", action="store_true")

    sub.add_parser("fetch", help="copy result files back from the cluster")

    p = sub.add_parser("publish", help="commit and push results")
    p.add_argument("-m", "--message", default="")

    p = sub.add_parser("pipeline", help="sync, submit, wait, fetch, publish")
    p.add_argument("-m", "--message", required=True)
    p.add_argument("--script", default="scripts/train.sbatch")
    p.add_argument("--poll", type=int, default=60)
    p.add_argument("--max-wait", type=int, default=240)

    p = sub.add_parser("serve", help="run the dashboard")
    p.add_argument("-p", "--port", type=int, default=8787)
    p.add_argument("--no-browser", action="store_true")

    a = ap.parse_args(argv)
    cfg, root = load_config(a.config if a.config.exists() else None)
    repo = (root / cfg.get("repo_path", ".")).resolve()

    if a.cmd == "serve":
        serve(a.config if a.config.exists() else None, a.port, not a.no_browser)
        return 0

    if a.cmd == "status":
        h = cfg.get("hpc", {})
        data = {
            "git": adapters.git_status(repo).as_dict(),
            "ssh": adapters.ssh_status(h.get("host", ""), h.get("user", "")).as_dict(),
            "allocation": h.get("allocation", {}),
            "results": adapters.list_results(
                repo / cfg.get("results_dir", "results")).as_dict()["data"],
        }
        if a.json:
            _out(data, True)
        else:
            g, s = data["git"], data["ssh"]
            print(f"repo    {repo}")
            if g["available"]:
                d = g["data"]
                print(f"branch  {d['branch']}  ({d['dirty_count']} uncommitted)")
                print(f"commit  {d.get('last_commit', {}).get('sha', '?')} "
                      f"{d.get('last_commit', {}).get('subject', '')}")
            else:
                print(f"git     unavailable: {g['reason']}")
            print(f"cluster {'connected' if s['available'] else 'not connected'}"
                  f"{'' if s['available'] else ' — ' + str(s['reason'])}")
            al = data["allocation"]
            if al:
                u = al.get("used", {})
                print(f"alloc   {al.get('name','?')}: "
                      f"CPU {u.get('cpu_hours',0)}/{al.get('cpu_hours','?')} h, "
                      f"GPU {u.get('gpu_hours',0)}/{al.get('gpu_hours','?')} h, "
                      f"expires {al.get('expires','?')}")
            print(f"results {len(data['results'])} file(s)")
        return 0

    if a.cmd == "jobs":
        r = ops.job_status(cfg)
        if a.json:
            _out(r, True)
        else:
            rows = r.data or []
            if not rows:
                print(r.message if not r.ok else "no jobs in the queue")
            else:
                print(f"{'id':<10}{'name':<22}{'state':<12}{'elapsed':<10}reason")
                for x in rows:
                    print(f"{x.get('id',''):<10}{x.get('name','')[:21]:<22}"
                          f"{x.get('state',''):<12}{x.get('elapsed',''):<10}"
                          f"{x.get('reason','')}")
        return 0 if r.ok else 1

    if a.cmd == "submit":
        env = dict(kv.split("=", 1) for kv in a.env if "=" in kv)
        r = ops.submit_job(cfg, a.script, env)
    elif a.cmd == "tail":
        r = ops.tail_job(cfg, a.job_id, a.lines)
    elif a.cmd == "cancel":
        r = ops.cancel_job(cfg, a.job_id)
    elif a.cmd == "sync":
        r = ops.sync_code(cfg, repo, a.message, run_tests=not a.skip_tests)
    elif a.cmd == "fetch":
        r = ops.fetch_results(cfg, repo)
    elif a.cmd == "publish":
        r = ops.publish_results(cfg, repo, a.message)
    elif a.cmd == "pipeline":
        r = ops.run_pipeline(cfg, repo, a.message, a.script, a.poll, a.max_wait,
                             progress=None if a.json else
                             (lambda m: print(f"  … {m}", flush=True)))
    else:  # pragma: no cover
        ap.error(f"unknown command {a.cmd}")

    _out(r, a.json)
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())

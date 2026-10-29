"""Variance structure and spatial-block CV power for Twente 5 cm SM, 2017-2020.

Run after sm_stats.py, in the directory holding its sm_series_*.json files.
See PLAN.md, "Power check", for the results and the decisions they fix.
"""
import glob, json, math, statistics as st, sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")


sys.path.insert(0, str(Path(__file__).resolve().parent))
from blocks import LOC, kmeans  # noqa: E402  (re-exported for eastwest_blocks.py)

SENSOR_RMSE = 0.025

S = {}
for f in glob.glob("sm_series_ITCSM_*.json"):
    name = f[10:18]
    raw = json.load(open(f))
    S[name] = {datetime.strptime(k, "%d-%m-%Y %H:%M"): v for k, v in raw.items()}
S = {k: v for k, v in S.items() if len(v) > 1000}       # stations with >1000 usable slots
print(f"stations with usable 2017-2020 data: {len(S)} -> {sorted(S)}")

# ---------------------------------------------------------------- variance structure
allv = [v for s in S.values() for v in s.values()]
tot = st.pvariance(allv)
means = {k: st.mean(v.values()) for k, v in S.items()}
between = st.pvariance([means[k] for k in S for _ in S[k]])
clim = {}                                                # station x month climatology
for k, s in S.items():
    bm = defaultdict(list)
    for t, v in s.items():
        bm[t.month].append(v)
    clim[k] = {m: st.mean(x) for m, x in bm.items()}
seasonal = st.pvariance([clim[k][t.month] - means[k] for k in S for t in S[k]])
anom = {k: {t: v - clim[k][t.month] for t, v in s.items()} for k, s in S.items()}
anomaly = st.pvariance([a for x in anom.values() for a in x.values()])
print(f"\nTOTAL variance {tot:.5f} (sd {math.sqrt(tot):.3f}); sensor error var {SENSOR_RMSE**2:.5f}")
print(f"  between-station means : {between/tot:6.1%}")
print(f"  seasonal cycle         : {seasonal/tot:6.1%}")
print(f"  anomalies (weather)    : {anomaly/tot:6.1%}  sd {math.sqrt(anomaly):.3f}"
      f" -> anomaly var / sensor err var = {anomaly/SENSOR_RMSE**2:.1f}")

# cross-station correlation of anomalies at shared times: how 'independent' are stations?
keys = sorted(S)
cors = []
for i, a in enumerate(keys):
    for b in keys[i+1:]:
        common = sorted(set(anom[a]) & set(anom[b]))
        if len(common) > 500:
            x = [anom[a][t] for t in common]; y = [anom[b][t] for t in common]
            mx, my = st.mean(x), st.mean(y)
            c = sum((p-mx)*(q-my) for p, q in zip(x, y)) / math.sqrt(sum((p-mx)**2 for p in x) * sum((q-my)**2 for q in y))
            cors.append(c)
print(f"  anomaly correlation between station pairs: median {st.median(cors):.2f} "
      f"(IQR {sorted(cors)[len(cors)//4]:.2f}-{sorted(cors)[3*len(cors)//4]:.2f}, {len(cors)} pairs)")

# ---------------------------------------------------------------- spatial blocks
def r2(pairs):
    y = [a for a, _ in pairs]; my = st.mean(y)
    ss = sum((a-my)**2 for a in y)
    return 1 - sum((a-b)**2 for a, b in pairs) / ss if ss else float("nan")

def evaluate(blocks, lag_days, target):
    """Held-out-block R2 of predicting each held-out station from the training
    stations' mean (anomaly or absolute) at time t - lag."""
    out = []
    for test in blocks:
        train = [n for n in S if n not in test]
        pairs = []
        for n in test:
            src = anom if target == "anomaly" else S
            for t, v in src[n].items():
                tt = t - timedelta(days=lag_days)
                vals = [src[m][tt] for m in train if tt in src[m]]
                if len(vals) >= 3:
                    pairs.append((v, st.mean(vals)))
        out.append(r2(pairs))
    return out

names = sorted(S)
print("\n== spatial block CV with a no-satellite predictor (mean of training stations)")
for k in (3, 4, 5):
    for target in ("absolute", "anomaly"):
        rows = []
        for seed in range(5):
            blocks = kmeans(names, k, seed)
            fresh = evaluate(blocks, 0, target); stale = evaluate(blocks, 5, target)
            rows.append((blocks, fresh, stale))
        sizes = sorted(len(b) for b in rows[0][0])
        mf = [st.mean(f) for _, f, _ in rows]; ms = [st.mean(s) for _, _, s in rows]
        deltas = [f - s for _, F, S_ in rows for f, s in zip(F, S_)]
        allf = [f for _, F, _ in rows for f in F]
        print(f"k={k} blocks (sizes {sizes}) target={target:8s}: fresh R2 mean {st.mean(mf):.3f} "
              f"[fold range {min(allf):.2f}..{max(allf):.2f}], 5-day-stale R2 {st.mean(ms):.3f}, "
              f"paired delta per fold {st.mean(deltas):.3f} +/- {st.pstdev(deltas):.3f} "
              f"(min {min(deltas):.3f}), across-partition sd of mean R2 {st.pstdev(mf):.3f}")

loso = [[n] for n in names]
for target in ("absolute", "anomaly"):
    f = evaluate(loso, 0, target); s = evaluate(loso, 5, target)
    d = [a - b for a, b in zip(f, s)]
    print(f"LOSO ({len(names)} folds) target={target:8s}: fresh R2 median {st.median(f):.3f} "
          f"[range {min(f):.2f}..{max(f):.2f}], stale {st.median(s):.3f}, "
          f"delta {st.mean(d):.3f} +/- {st.pstdev(d):.3f}, folds where stale wins: {sum(x < 0 for x in d)}")

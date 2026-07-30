"""Backtest FPL's own expected-points figure against what actually happened.

Source: the vaastav/Fantasy-Premier-League archive, whose per-gameweek files
carry an `xP` column (FPL's expected points as published for that gameweek)
alongside `total_points` (what the player actually scored).

Spearman is computed as Pearson-on-ranks so this does not need scipy, which is
ABI-broken against the installed numpy here.
"""
import numpy as np
import pandas as pd


def spearman(a, b):
    return a.rank().corr(b.rank())


def score(name, pred, actual):
    err = pred - actual
    return {
        "model": name,
        "r": pred.corr(actual),
        "rho": spearman(pred, actual),
        "MAE": err.abs().mean(),
        "RMSE": float(np.sqrt((err ** 2).mean())),
        "bias": err.mean(),
    }


for season in ["2024-25", "2023-24"]:
    d = pd.read_csv(f"merged_{season}.csv")
    for c in ["xP", "total_points", "minutes", "round", "element"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["xP", "total_points", "round", "element"]).sort_values(["element", "round"])

    # Baselines built only from information available before the gameweek.
    d["prev_pts"] = d.groupby("element")["total_points"].shift(1)
    d["roll3"] = d.groupby("element")["total_points"].shift(1).rolling(3, min_periods=1).mean()
    d["expanding"] = (d.groupby("element")["total_points"]
                       .transform(lambda s: s.shift(1).expanding().mean()))

    print("=" * 86)
    print(f"{season}   rows={len(d)}  gameweeks={d['round'].nunique()}  players={d['element'].nunique()}")

    for label, sub in [
        ("everyone", d),
        ("played (mins>0)", d[d["minutes"] > 0]),
        ("started (mins>=60)", d[d["minutes"] >= 60]),
    ]:
        s = sub.dropna(subset=["prev_pts", "roll3", "expanding"])
        rows = [
            score("FPL xP", s["xP"], s["total_points"]),
            score("last GW's points", s["prev_pts"], s["total_points"]),
            score("mean of last 3", s["roll3"], s["total_points"]),
            score("season avg to date", s["expanding"], s["total_points"]),
        ]
        print(f"\n  -- {label}  (n={len(s)}) " + "-" * (62 - len(label)))
        print(f"     {'model':<22}{'r':>7}{'rho':>8}{'MAE':>8}{'RMSE':>8}{'bias':>8}")
        for r in rows:
            print(f"     {r['model']:<22}{r['r']:>7.3f}{r['rho']:>8.3f}"
                  f"{r['MAE']:>8.2f}{r['RMSE']:>8.2f}{r['bias']:>+8.2f}")

    # Calibration: when FPL says N points, what actually lands?
    s = d[d["minutes"] > 0].copy()
    s["band"] = pd.cut(s["xP"], [-0.01, 1, 2, 3, 4, 5, 6, 100],
                       labels=["0-1", "1-2", "2-3", "3-4", "4-5", "5-6", "6+"])
    cal = s.groupby("band", observed=True)["total_points"].agg(["count", "mean"])
    cal["xP_mid"] = s.groupby("band", observed=True)["xP"].mean()
    print(f"\n  -- calibration, players who featured --")
    print(f"     {'xP band':<10}{'n':>7}{'mean xP':>10}{'mean actual':>13}{'gap':>8}")
    for band, row in cal.iterrows():
        print(f"     {str(band):<10}{int(row['count']):>7}{row['xP_mid']:>10.2f}"
              f"{row['mean']:>13.2f}{row['xP_mid'] - row['mean']:>+8.2f}")
    print()

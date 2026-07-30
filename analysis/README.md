# analysis/

One-off analyses. Nothing here runs on a schedule and nothing here is imported
by `logger/` or `dashboard/` — rule 1 stays intact.

## backtest_xp.py

Measures how good FPL's own expected-points figure actually is, against two full
seasons of outcomes (~57,000 player-gameweeks). Findings are written up in
HANDOFF.md §10; the short version is that it beats every naive baseline but is
worth r≈0.52 among players who actually featured, with a ~1 point downward bias
on premiums.

`ep_next` is **not** archived by the FPL API — neither `element-summary` history
array carries it. This works because the community archive at
`vaastav/Fantasy-Premier-League` preserves it as an `xP` column.

```bash
cd analysis
curl -sLO https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/2024-25/gws/merged_gw.csv
mv merged_gw.csv merged_2024-25.csv
curl -sLO https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/2023-24/gws/merged_gw.csv
mv merged_gw.csv merged_2023-24.csv
python backtest_xp.py
```

The downloaded CSVs are gitignored — they are ~5 MB each and belong to that
project, not this one.

"""List renders/ and flag any whose data date is older than the current data date.

Renders are named renders/NN-slug_YYYY-MM-DD.png, where NN is the camera number
(01-20) and the date is the data date: max(last_checked) in data/proposals.csv
at render time. Exits 1 if any render is stale or misnamed.
"""
import csv
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDERS = ROOT / "renders"
PROPOSALS = ROOT / "data" / "proposals.csv"
NAME_RE = re.compile(r"^(?P<nn>\d{2})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)_(?P<date>\d{4}-\d{2}-\d{2})\.png$")


def current_data_date():
    if not PROPOSALS.exists():
        return None
    with PROPOSALS.open(newline="", encoding="utf-8") as f:
        dates = [date.fromisoformat(row["last_checked"].strip())
                 for row in csv.DictReader(f) if (row.get("last_checked") or "").strip()]
    return max(dates, default=None)


def main():
    data_date = current_data_date()
    if data_date:
        print(f"Current data date (max last_checked): {data_date}")
    else:
        print("WARNING: no last_checked dates in data/proposals.csv; cannot check staleness")

    # renders/renders.json and renders/thumbs/ belong to the gallery page (web/renders.html), not renders
    files = sorted(p for p in RENDERS.glob("*") if p.is_file() and p.name not in (".gitkeep", "renders.json")) \
        if RENDERS.exists() else []
    if not files:
        print("renders/ is empty")
        return 0

    problems = 0
    for p in files:
        m = NAME_RE.match(p.name)
        status = "ok"
        if not m or not 1 <= int(m["nn"]) <= 20:
            status = "BAD NAME (want NN-slug_YYYY-MM-DD.png, NN 01-20)"
        else:
            try:
                render_date = date.fromisoformat(m["date"])
            except ValueError:
                render_date = None
                status = "BAD DATE"
            if render_date and data_date and render_date < data_date:
                status = f"STALE (data date {data_date})"
        if status != "ok":
            problems += 1
        print(f"{status:<40} {p.relative_to(ROOT)}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

"""Command-line interface.

    python -m evgraph investigate example.com [--fetch-site]
    python -m evgraph import observations.jsonl
    python -m evgraph conclusions
    python -m evgraph report > report.md
    python -m evgraph demo
    python -m evgraph serve
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .collect import import_jsonl, investigate
from .demo import load
from .service import to_markdown, view
from .store import Store


def _conclusions(st: Store) -> None:
    for h in view(st)["hypotheses"]:
        print(f"  {round(h['posterior'] * 100):>3}%  {h['a_label']} ↔ {h['b_label']}  [{h['status']}]")
        for f in h["factors"][:4]:
            print(f"         {f['contribution']:+.2f}  {f['rule']:<26} via {f['via']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evgraph", description="OSINT Evidence Graph with confidence scoring")
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("investigate")
    i.add_argument("domains", nargs="+")
    i.add_argument("--fetch-site", action="store_true", help="also fetch the public homepage (contacts the target)")
    im = sub.add_parser("import")
    im.add_argument("file")
    sub.add_parser("conclusions")
    sub.add_parser("report")
    sub.add_parser("demo")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("evgraph.app:app", host=a.host, port=a.port, reload=a.reload)
        return 0
    st = Store(a.db)
    if a.cmd == "investigate":
        for d in a.domains:
            r = investigate(st, d, fetch_site=a.fetch_site)
            print(f"{d}: {r['observations_added']} observations" + "".join(f"\n  ! {e}" for e in r["errors"]))
    elif a.cmd == "import":
        print(f"imported {import_jsonl(st, Path(a.file).read_text())} observations")
    elif a.cmd == "conclusions":
        _conclusions(st)
    elif a.cmd == "report":
        sys.stdout.write(to_markdown(view(st)))
    elif a.cmd == "demo":
        st.reset()
        load(st)
        _conclusions(st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

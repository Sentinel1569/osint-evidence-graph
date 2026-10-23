"""Workspace view and Markdown report shared by API and CLI."""

from __future__ import annotations

from .evidence import utcnow
from .reasoning import DEFAULT_PRIOR, RULES, hypotheses
from .store import Store


def pct(p: float) -> str:
    return ">99%" if p > 0.99 else "<1%" if p < 0.01 else f"{round(p * 100)}%"


def view(store: Store) -> dict:
    ents, obs = store.entities(), store.observations()
    rules = store.rules(RULES)
    prior = store.setting("prior", DEFAULT_PRIOR)
    hyps = hypotheses(ents, obs, rules, prior, store.decisions(), seeds=store.setting("seeds", []))
    return {"entities": ents, "observations": obs, "hypotheses": hyps, "rules": rules, "prior": prior, "seeds": store.setting("seeds", []),
            "summary": {"entities": len(ents), "observations": len(obs), "sources": len({o["source"] for o in obs}),
                        "hypotheses": len(hyps), "accepted": sum(h["status"] == "accepted" for h in hyps),
                        "likely": sum(h["posterior"] >= 0.55 for h in hyps)}}


def to_markdown(v: dict) -> str:
    obs = {o["id"]: o for o in v["observations"]}
    ents = {e["id"]: e for e in v["entities"]}
    out = ["# Evidence graph report", "", f"_Generated {utcnow()} UTC · {v['summary']['observations']} observations from "
           f"{v['summary']['sources']} sources · prior {round(v['prior'] * 100)}%_", "",
           "Observations are what sources reported. Conclusions below are hypotheses scored with likelihood ratios; each lists the "
           "evidence for and against it.", "", "## Conclusions", ""]
    for h in v["hypotheses"]:
        out += [f"### {h['a_label']} ↔ {h['b_label']}: same operator? **{pct(h['posterior'])}** ({h['label']}) · {h['status']}", "",
                h["explanation"], "", "| Evidence | Via | LR | Confidence | Δ log-odds | Sources |", "|---|---|---:|---:|---:|---|"]
        for f in h["factors"]:
            out.append(f"| {f['rule']} | {f['via']} | {f['lr']} | {round(f['conf'] * 100)}% | {f['contribution']:+.2f} | {', '.join(f['sources'])} |")
        if h["decision"]:
            out.append(f"\n_Analyst {h['decision'].get('analyst') or '—'} on {h['decision'].get('decided_at', '')}: {h['decision'].get('note', '')}_")
        out.append("")
    out += ["## Observations", "", "| Subject | Predicate | Object | Source | Grade | Retrieved | SHA-256 |", "|---|---|---|---|---|---|---|"]
    for o in obs.values():
        out.append(f"| {ents.get(o['subject'], {}).get('label', o['subject'])} | {o['predicate']} | {ents.get(o['object'], {}).get('label', o['object'])} | "
                   f"{o['source']} | {o['grade']} | {o['retrieved_at'][:19]} | `{(o['sha256'] or '')[:12]}…` |")
    out += ["", "## Rulebook (likelihood ratios)", "", "| Rule | LR | Meaning |", "|---|---:|---|"]
    out += [f"| {k} | {r['lr']} | {r['description']} |" for k, r in v["rules"].items()]
    return "\n".join(out) + "\n"

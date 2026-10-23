"""From observations to explained conclusions, using likelihood ratios.

Observations are facts *about sources* ("crt.sh lists certificate X covering
a.example and b.example"). Conclusions such as "a.example and b.example are
run by the same operator" are never stored as observations. They are
**hypotheses**, scored with Bayes' rule in log-odds form::

    logit(posterior) = logit(prior) + Σ  confidenceᵢ · ln(LRᵢ)

* ``LR`` (likelihood ratio) comes from an editable **rulebook**: how much more
  likely the evidence is if the hypothesis is true than if it is false. A
  shared CDN IP has an LR near 1 (it happens to strangers all the time); a
  shared analytics ID has a high LR.
* ``confidence`` is the Admiralty-derived confidence of the observation(s)
  the evidence rests on. Weak sources move the needle less.
* Repeated evidence of the same kind is discounted (each further factor of
  one rule counts half as much as the previous), because it is not
  independent.
* Contradicting evidence (LR < 1) pulls the posterior down.

The engine proposes hypotheses. Analysts accept or reject them, and the
reasoning at the moment of decision is snapshotted.
"""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations

from .evidence import confidence_label

DEFAULT_PRIOR = 0.1
RULES = {
    "shared_analytics_id": {"lr": 25.0, "description": "Same web-analytics / tag-manager ID: normally configured by one account owner."},
    "same_registrant": {"lr": 15.0, "description": "Same non-redacted registrant organisation in registration data."},
    "redirects_to": {"lr": 8.0, "description": "One domain redirects to the other."},
    "shared_certificate": {"lr": 4.0, "description": "Both names on one TLS certificate: someone requested both, usually the same operator."},
    "shared_certificate_bulk": {"lr": 1.2, "description": "Both names on a large multi-tenant certificate (>50 names): common on CDNs and hosting."},
    "shared_ip": {"lr": 3.0, "description": "Resolve to the same IP on dedicated infrastructure."},
    "shared_ip_shared_hosting": {"lr": 1.1, "description": "Same IP, but it belongs to a CDN or shared host where unrelated sites co-reside."},
    "shared_mx": {"lr": 3.0, "description": "Same self-hosted mail exchanger."},
    "shared_mx_provider": {"lr": 1.0, "description": "Same large mail provider: uninformative."},
    "shared_nameserver": {"lr": 1.5, "description": "Same self-hosted nameservers."},
    "shared_nameserver_provider": {"lr": 1.0, "description": "Same large DNS provider: uninformative."},
    "links_to": {"lr": 2.0, "description": "One site links to the other."},
    "same_privacy_service": {"lr": 1.0, "description": "Both use a registration privacy service: uninformative."},
    "different_registrant": {"lr": 0.25, "description": "Different non-redacted registrant organisations: evidence against."},
}
SHARED = {  # predicate on (domain -> X) → (rule, rule when X is flagged as shared infrastructure, flag attribute)
    "has_certificate": ("shared_certificate", "shared_certificate_bulk", "bulk"),
    "resolves_to": ("shared_ip", "shared_ip_shared_hosting", "shared_infra"),
    "uses_analytics_id": ("shared_analytics_id", None, None),
    "registrant_org": ("same_registrant", "same_privacy_service", "privacy"),
    "uses_mx": ("shared_mx", "shared_mx_provider", "big_provider"),
    "uses_nameserver": ("shared_nameserver", "shared_nameserver_provider", "big_provider"),
}
DIRECT = {"links_to": "links_to", "redirects_to": "redirects_to"}


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def hypotheses(entities: list[dict], observations: list[dict], rules: dict, prior: float = DEFAULT_PRIOR, decisions: dict | None = None,
               seeds: list[str] | None = None, min_factors: int = 1) -> list[dict]:
    """Score same-operator hypotheses for domain pairs.

    With ``seeds`` (the domains an analyst investigated) a pair is proposed when both are seeds, or when one is
    a seed and the pair has at least one informative factor (|LR| ≥ 1.5). Subdomains of a seed are folded
    into it. This keeps a 60-name CDN certificate from producing 1,770 meaningless hypotheses.
    """
    decisions = decisions or {}
    ent = {e["id"]: e for e in entities}
    by_subject: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for o in observations:
        by_subject[o["subject"]][o["predicate"]].append(o)
    domains = sorted(e["id"] for e in entities if e["type"] == "domain")
    pairs = set()
    holders: dict[tuple[str, str], set[str]] = defaultdict(set)
    for o in observations:
        if o["predicate"] in SHARED and o["subject"] in ent and ent[o["subject"]]["type"] == "domain":
            holders[(o["predicate"], o["object"])].add(o["subject"])
        if o["predicate"] in DIRECT and o["subject"] in domains and o["object"] in domains:
            pairs.add(tuple(sorted((o["subject"], o["object"]))))
    for hs in holders.values():
        pairs.update(combinations(sorted(hs), 2))

    seed_ids = {f"domain:{x}" for x in (seeds or [])}

    def folded(x):
        return any(x != sd and x.endswith("." + sd.split(":", 1)[1]) for sd in seed_ids)
    out = []
    for a, b in sorted(pairs):
        if seed_ids and (folded(a) or folded(b) or not ({a, b} & seed_ids)):
            continue
        factors = []
        for pred, (rule, weak_rule, flag) in SHARED.items():
            oa = {o["object"]: o for o in by_subject[a].get(pred, [])}
            ob = {o["object"]: o for o in by_subject[b].get(pred, [])}
            for x in sorted(set(oa) & set(ob)):
                target = ent.get(x, {"attrs": {}, "label": x})
                r = weak_rule if (flag and target["attrs"].get(flag)) else rule
                factors.append({"rule": r, "conf": min(oa[x]["confidence"], ob[x]["confidence"]), "observations": [oa[x]["id"], ob[x]["id"]],
                                "via": target.get("label", x), "sources": sorted({oa[x]["source"], ob[x]["source"]})})
            if pred == "registrant_org":
                da = [o for x, o in oa.items() if x not in ob and not ent.get(x, {"attrs": {}})["attrs"].get("privacy")]
                db = [o for x, o in ob.items() if x not in oa and not ent.get(x, {"attrs": {}})["attrs"].get("privacy")]
                if da and db:
                    factors.append({"rule": "different_registrant", "conf": min(da[0]["confidence"], db[0]["confidence"]),
                                    "observations": [da[0]["id"], db[0]["id"]], "sources": sorted({da[0]["source"], db[0]["source"]}),
                                    "via": f"{ent.get(da[0]['object'], {}).get('label', da[0]['object'])} ≠ {ent.get(db[0]['object'], {}).get('label', db[0]['object'])}"})
        for pred, rule in DIRECT.items():
            for s, t in ((a, b), (b, a)):
                for o in by_subject[s].get(pred, []):
                    if o["object"] == t:
                        factors.append({"rule": rule, "conf": o["confidence"], "observations": [o["id"]], "sources": [o["source"]],
                                        "via": f"{ent[s]['label']} → {ent[t]['label']}"})
        if len(factors) < min_factors:
            continue
        if seed_ids and not {a, b} <= seed_ids and not any(abs(math.log(rules[f["rule"]]["lr"])) >= math.log(1.5) for f in factors):
            continue
        seen_rules: dict[str, int] = defaultdict(int)
        total = logit(prior)
        for f in sorted(factors, key=lambda f: -abs(math.log(rules[f["rule"]]["lr"])) * f["conf"]):
            lr = rules[f["rule"]]["lr"]
            discount = 0.5 ** seen_rules[f["rule"]]
            seen_rules[f["rule"]] += 1
            f["lr"] = lr
            f["discount"] = discount
            f["contribution"] = round(f["conf"] * discount * math.log(lr), 4)
            f["description"] = rules[f["rule"]]["description"]
            total += f["contribution"]
        post = sigmoid(total)
        hid = f"same_operator|{a}|{b}"
        dec = decisions.get(hid, {})
        out.append({"id": hid, "kind": "same_operator", "a": a, "b": b, "a_label": ent[a]["label"], "b_label": ent[b]["label"],
                    "prior": prior, "posterior": round(post, 4), "label": confidence_label(post), "log_odds": round(total, 4),
                    "factors": sorted(factors, key=lambda f: -abs(f["contribution"])), "status": dec.get("status", "open"),
                    "decision": {k: dec[k] for k in ("note", "analyst", "decided_at") if k in dec},
                    "independent_sources": len({s for f in factors for s in f["sources"]}),
                    "explanation": _explain(ent[a]["label"], ent[b]["label"], prior, post, factors)})
    return sorted(out, key=lambda h: -h["posterior"])


def _explain(a: str, b: str, prior: float, post: float, factors: list[dict]) -> str:
    pos = [f for f in factors if f["contribution"] > 0.05]
    neg = [f for f in factors if f["contribution"] < -0.05]
    s = f"Starting from a {round(prior * 100)}% prior that {a} and {b} share an operator, "
    if pos:
        s += "the evidence for it is " + "; ".join(f"{f['rule'].replace('_', ' ')} ({f['via']})" for f in pos[:3])
    else:
        s += "no observation meaningfully supports it"
    if neg:
        s += ". Against it: " + "; ".join(f"{f['rule'].replace('_', ' ')} ({f['via']})" for f in neg[:2])
    weak = [f for f in factors if abs(f["contribution"]) <= 0.05]
    if weak:
        s += f". {len(weak)} observation(s) were recorded but carry almost no weight (e.g. {weak[0]['rule'].replace('_', ' ')})"
    shown = ">99%" if post > 0.99 else "<1%" if post < 0.01 else f"{round(post * 100)}%"
    return s + f". Posterior: {shown} ({confidence_label(post)})."

import math

from fastapi.testclient import TestClient

from evgraph.app import create_app
from evgraph.collect import import_jsonl, investigate
from evgraph.demo import DemoProvider, load
from evgraph.reasoning import RULES, hypotheses, logit, sigmoid
from evgraph.service import to_markdown, view
from evgraph.store import Store


def _ws():
    st = Store(":memory:")
    load(st)
    return st, {(h["a_label"], h["b_label"]): h for h in view(st)["hypotheses"]}


def test_logodds_helpers():
    assert abs(sigmoid(logit(0.3)) - 0.3) < 1e-9


def test_observations_have_provenance_and_are_idempotent():
    st = Store(":memory:")
    investigate(st, "acme-shop.example", DemoProvider(), fetch_site=True)
    n = len(st.observations())
    investigate(st, "acme-shop.example", DemoProvider(), fetch_site=True)
    assert len(st.observations()) == n
    for o in st.observations():
        assert o["source"] and o["retrieved_at"] and o["sha256"] and 0 < o["confidence"] <= 1


def test_shared_certificate_is_recorded_not_concluded():
    st, h = _ws()
    # the observation exists ...
    obs = [o for o in st.observations() if o["predicate"] == "has_certificate" and o["subject"] == "domain:acme-store.example"]
    assert obs
    # ... and no "same owner" fact was stored anywhere
    assert not any(o["predicate"] in ("same_owner", "same_operator") for o in st.observations())
    # the conclusion is a scored hypothesis
    assert ("acme-shop.example", "acme-store.example") in h


def test_strong_and_weak_cases():
    _, h = _ws()
    strong = h[("acme-shop.example", "acme-store.example")]
    assert strong["posterior"] > 0.95 and strong["independent_sources"] >= 3
    outlet = h[("acme-outlet.example", "acme-shop.example")]
    assert outlet["posterior"] > 0.8  # privacy-redacted, but shared analytics + redirect
    cdn = h[("acme-deals.example", "unrelated-blog.example")]
    assert cdn["posterior"] < 0.2  # bulk cert + CDN IP barely move the needle
    assert {f["rule"] for f in cdn["factors"]} >= {"shared_certificate_bulk", "shared_ip_shared_hosting"}
    deals = h[("acme-deals.example", "acme-shop.example")]
    assert any(f["rule"] == "different_registrant" and f["contribution"] < 0 for f in deals["factors"])
    assert deals["posterior"] < deals["prior"]


def test_bulk_certificate_does_not_explode_hypotheses():
    _, h = _ws()
    assert not any("cdn-customer" in a or "cdn-customer" in b for a, b in h)
    assert not any(a.startswith("www.") or b.startswith("www.") for a, b in h)


def test_contribution_math_and_repeat_discount():
    st, h = _ws()
    f = h[("acme-shop.example", "acme-store.example")]["factors"][0]
    assert math.isclose(f["contribution"], round(f["conf"] * f["discount"] * math.log(f["lr"]), 4))
    ents = [{"id": "domain:a.x", "type": "domain", "label": "a.x", "attrs": {}}, {"id": "domain:b.x", "type": "domain", "label": "b.x", "attrs": {}}]
    for ip in ("1", "2"):
        ents.append({"id": f"ip:{ip}", "type": "ip", "label": ip, "attrs": {}})
    obs = [{"id": f"{d}{ip}", "subject": f"domain:{d}", "predicate": "resolves_to", "object": f"ip:{ip}", "confidence": 1.0, "source": "s"}
           for d in ("a.x", "b.x") for ip in ("1", "2")]
    hs = hypotheses(ents, obs, RULES, 0.1)
    assert sorted(f["discount"] for f in hs[0]["factors"]) == [0.5, 1.0]


def test_rulebook_and_prior_change_the_answer():
    st, h = _ws()
    before = h[("acme-outlet.example", "acme-store.example")]["posterior"]
    st.set_rule("shared_analytics_id", 2.0, "skeptical analyst")
    st.set_setting("prior", 0.02)
    after = {(x["a_label"], x["b_label"]): x for x in view(st)["hypotheses"]}[("acme-outlet.example", "acme-store.example")]["posterior"]
    assert after < before


def test_import_and_report():
    st, _ = _ws()
    n = import_jsonl(st, open("samples/observations.jsonl").read())
    assert n == 3
    md = to_markdown(view(st))
    assert "## Conclusions" in md and "## Rulebook" in md and "Passive DNS export" in md


def test_api(tmp_path):
    c = TestClient(create_app(str(tmp_path / "e.db")))
    c.post("/api/demo")
    w = c.get("/api/workspace").json()
    assert w["summary"]["hypotheses"] >= 5
    hid = w["hypotheses"][0]["id"]
    assert c.post("/api/decide", json={"hypothesis": hid, "status": "accepted", "note": "verified WHOIS history"}).status_code == 200
    assert c.get("/api/workspace").json()["hypotheses"][0]["status"] == "accepted"
    assert c.post("/api/decide", json={"hypothesis": "nope", "status": "accepted"}).status_code == 404
    assert c.post("/api/rules", json={"rule": "shared_ip", "lr": 0}).status_code == 422
    assert c.post("/api/prior", json={"prior": 2}).status_code == 422
    assert c.post("/api/investigate", json={"domain": "not a domain"}).status_code == 422
    assert c.post("/api/observations", json={"subject_type": "domain", "subject": "a.example", "predicate": "links_to",
                                             "object_type": "domain", "object": "b.example", "source": " "}).status_code == 422
    assert c.get("/api/report.md").status_code == 200
    assert c.get("/").status_code == 200

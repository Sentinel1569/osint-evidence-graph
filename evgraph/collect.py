"""Collectors that turn public data into *observations* (never conclusions).

``investigate(store, domain)`` records, for one domain:

* certificates from Certificate Transparency and every name they cover
* A / NS / MX records via DNS-over-HTTPS, flagging CDN or large-provider
  infrastructure (which is weak evidence of common ownership)
* the registrant organisation from RDAP, flagging privacy/redaction services
* optionally (``fetch_site=True``) the public homepage: analytics/tag IDs,
  outbound links and redirects. This is ordinary web browsing, but it does
  contact the target, so it is off by default.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .evidence import Evidence
from .http import FetchError, fetch, fetch_json
from .store import Store, entity_id

BIG_DNS = re.compile(r"(cloudflare\.com|awsdns|googledomains|google\.com|azure-dns|nsone\.net|domaincontrol\.com|registrar-servers\.com|"
                     r"ultradns|dnsmadeeasy|akam\.net|dns-host\.example)$", re.I)
BIG_MX = re.compile(r"(google\.com|googlemail\.com|outlook\.com|protection\.outlook\.com|mimecast\.com|pphosted\.com|zoho\.|"
                    r"messagelabs\.com|mailgun\.org|bulkmail\.example|bigmail\.example)$", re.I)
CDN_ORG = re.compile(r"cloudflare|akamai|fastly|amazon|google|microsoft|incapsula|imperva|edgecast|stackpath|cdn", re.I)
PRIVACY = re.compile(r"privacy|redacted|proxy|whoisguard|withheld|protected|data protected|not disclosed", re.I)
ANALYTICS = re.compile(r"\b(UA-\d{4,10}-\d{1,4}|G-[A-Z0-9]{8,12}|GTM-[A-Z0-9]{4,9}|AW-\d{6,12}|ca-pub-\d{10,20})\b")
HREF = re.compile(r"""href=["'](https?://[^"'#\s>]+)""", re.I)


class LiveProvider:
    def crtsh(self, domain):
        return fetch_json("https://crt.sh/", params={"q": domain, "output": "json"}, source="crt.sh Certificate Transparency",
                          reliability="A", credibility=2, ttl=6 * 3600, timeout=90)

    def doh(self, name, rtype):
        return fetch_json("https://dns.google/resolve", params={"name": name, "type": rtype}, source="Google Public DNS (DoH)",
                          reliability="A", credibility=1, ttl=1800)

    def rdap_domain(self, domain):
        return fetch_json(f"https://rdap.org/domain/{domain}", source="RDAP (domain registry)", reliability="A", credibility=2, ttl=86400)

    def rdap_ip(self, ip):
        return fetch_json(f"https://rdap.org/ip/{ip}", source="RDAP (IP registry)", reliability="A", credibility=2, ttl=86400)

    def homepage(self, domain):
        body, ev = fetch(f"https://{domain}/", source=f"{domain} homepage", reliability="B", credibility=2, ttl=3600)
        return ev.url, body.decode("utf-8", "replace"), ev


def _o(store: Store, ev: Evidence, s, p, o, why, **attrs):
    return store.observe(s, p, o, source=ev.source, url=ev.url, retrieved_at=ev.retrieved_at, sha256=ev.sha256, method=ev.method,
                         reliability=ev.reliability, credibility=ev.credibility, explanation=why, **attrs)


def _registrant(payload: dict) -> str:
    for ent in payload.get("entities", []):
        if "registrant" in (ent.get("roles") or []):
            vcard = (ent.get("vcardArray") or [None, []])[1]
            org = next((x[3] for x in vcard if x and x[0] == "org"), "") or next((x[3] for x in vcard if x and x[0] == "fn"), "")
            if isinstance(org, list):
                org = " ".join(org)
            return str(org).strip()
    return ""


def investigate(store: Store, domain: str, provider=None, *, fetch_site: bool = False, max_certs: int = 40) -> dict:
    provider = provider or LiveProvider()
    domain = domain.lower().strip().strip(".")
    d = store.entity("domain", domain, investigated=True)
    seeds = store.setting("seeds", [])
    if domain not in seeds:
        store.set_setting("seeds", seeds + [domain])
    added, errors = 0, []

    try:
        rows, ev = provider.crtsh(domain)
        seen = set()
        for r in sorted(rows if isinstance(rows, list) else [], key=lambda r: r.get("not_before", ""), reverse=True):
            key = (r.get("issuer_ca_id"), r.get("serial_number"))
            if key in seen or len(seen) >= max_certs:
                continue
            seen.add(key)
            names = sorted({n.strip().lower().lstrip("*.") for n in str(r.get("name_value", "")).split("\n") if n.strip()})
            cid = store.entity("certificate", str(r.get("serial_number") or r.get("id")), label=f"cert {str(r.get('serial_number') or r.get('id'))[:10]}…",
                               san_count=len(names), bulk=len(names) > 50, issuer=r.get("issuer_name", ""), not_before=r.get("not_before", ""))
            for n in names[:100]:
                nid = store.entity("domain", n)
                _o(store, ev, nid, "has_certificate", cid, f"Certificate Transparency log lists {n} on this certificate.", san_count=len(names))
                added += 1
            if domain not in names:
                _o(store, ev, d, "has_certificate", cid, "Certificate returned for this domain's CT query.")
    except FetchError as exc:
        errors.append(f"CT: {exc}")

    for rtype, pred, kind, flag_re in (("A", "resolves_to", "ip", None), ("NS", "uses_nameserver", "nameserver", BIG_DNS), ("MX", "uses_mx", "mailserver", BIG_MX)):
        try:
            payload, ev = provider.doh(domain, rtype)
        except FetchError as exc:
            errors.append(f"DNS {rtype}: {exc}")
            continue
        for a in payload.get("Answer") or []:
            val = str(a.get("data", "")).rstrip(".").split()[-1].rstrip(".")
            if not val or (rtype == "A" and a.get("type") != 1):
                continue
            attrs = {"big_provider": bool(flag_re.search(val))} if flag_re else {}
            if rtype == "A":
                try:
                    ip_payload, iev = provider.rdap_ip(val)
                    org = ip_payload.get("name", "") + " " + " ".join(
                        str(x[3]) for e in ip_payload.get("entities", []) for x in (e.get("vcardArray") or [None, []])[1] if x and x[0] == "fn")
                    attrs = {"network": org.strip(), "shared_infra": bool(CDN_ORG.search(org))}
                except FetchError:
                    pass
            xid = store.entity(kind, val, **attrs)
            _o(store, ev, d, pred, xid, f"DNS {rtype} record at collection time.")
            added += 1

    try:
        payload, ev = provider.rdap_domain(domain)
        org = _registrant(payload)
        if org:
            oid = store.entity("organization", org, privacy=bool(PRIVACY.search(org)))
            _o(store, ev, d, "registrant_org", oid, "Registrant organisation in the registry's RDAP record.")
            added += 1
    except FetchError as exc:
        errors.append(f"RDAP: {exc}")

    if fetch_site:
        try:
            final_url, html, ev = provider.homepage(domain)
            host = (urlsplit(final_url).hostname or domain).lower()
            if host != domain and not host.endswith("." + domain):
                _o(store, ev, d, "redirects_to", store.entity("domain", host), f"Homepage redirected to {final_url}.")
            for aid in sorted(set(ANALYTICS.findall(html))):
                _o(store, ev, d, "uses_analytics_id", store.entity("analytics_id", aid), f"Page source embeds {aid}.")
                added += 1
            for link_host in sorted({(urlsplit(u).hostname or "").lower() for u in HREF.findall(html)})[:40]:
                if link_host and link_host != domain and not link_host.endswith("." + domain):
                    _o(store, ev, d, "links_to", store.entity("domain", link_host), "Homepage links to this domain.")
        except FetchError as exc:
            errors.append(f"homepage: {exc}")
    return {"domain": domain, "observations_added": added, "errors": errors}


def import_jsonl(store: Store, text: str, source: str = "import") -> int:
    """Import observations from JSON lines: {"subject": ["domain","a.example"], "predicate": "...", "object": ["ip","192.0.2.1"],
    "source": "...", "url": "...", "reliability": "B", "credibility": 2, "explanation": "..."}"""
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        s = store.entity(*r["subject"][:2])
        o = store.entity(*r["object"][:2])
        store.observe(s, r["predicate"], o, source=r.get("source", source), url=r.get("url", ""), reliability=r.get("reliability", "F"),
                      credibility=int(r.get("credibility", 6)), explanation=r.get("explanation", ""), method="import",
                      observed_at=r.get("observed_at", ""))
        n += 1
    return n


__all__ = ["investigate", "import_jsonl", "LiveProvider", "entity_id"]

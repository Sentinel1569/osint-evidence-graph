"""Offline demo world returned in the raw formats of crt.sh, DoH, RDAP and HTML.

All domains use the reserved ``.example`` TLD and all IPs are RFC 5737
documentation addresses. The scenario covers the cases that matter:

* acme-shop / acme-store: shared certificate, analytics ID, registrant, self-hosted DNS
  and dedicated IP → strong same-operator conclusion
* acme-outlet: registration hidden behind a privacy service, but it shares the
  analytics ID and redirects to acme-shop → still strong
* acme-deals / unrelated-blog: same CDN IP and a 60-name multi-tenant certificate
  → recorded, but barely moves the posterior
* acme-deals vs acme-shop: an affiliate link, against a different registrant → stays low
"""

from __future__ import annotations

from .evidence import Evidence, sha256_json
from .http import FetchError

CDN_CUSTOMERS = [f"cdn-customer-{i}.example" for i in range(58)]
CERTS = [
    {"serial_number": "0a11ce5", "issuer_name": "C=US, O=Let's Encrypt, CN=R11", "not_before": "2026-08-01T00:00:00", "issuer_ca_id": 1,
     "name_value": "acme-shop.example\nwww.acme-shop.example\nacme-store.example"},
    {"serial_number": "0b0bca7", "issuer_name": "C=US, O=Example CDN CA", "not_before": "2026-07-15T00:00:00", "issuer_ca_id": 2,
     "name_value": "\n".join(["acme-deals.example", "unrelated-blog.example"] + CDN_CUSTOMERS)},
    {"serial_number": "0c0ffee", "issuer_name": "C=US, O=Let's Encrypt, CN=R10", "not_before": "2026-09-01T00:00:00", "issuer_ca_id": 1,
     "name_value": "acme-outlet.example"},
]
DNS = {
    "acme-shop.example": {"A": ["198.51.100.10"], "NS": ["ns1.acme-dns.example", "ns2.acme-dns.example"], "MX": ["10 mx.bigmail.example"]},
    "acme-store.example": {"A": ["198.51.100.10"], "NS": ["ns1.acme-dns.example"], "MX": ["10 mx.bigmail.example"]},
    "acme-outlet.example": {"A": ["198.51.100.77"], "NS": ["ns1.dns-host.example"], "MX": []},
    "acme-deals.example": {"A": ["203.0.113.50"], "NS": ["ns1.dns-host.example"], "MX": ["10 mx.bigmail.example"]},
    "unrelated-blog.example": {"A": ["203.0.113.50"], "NS": ["ns1.dns-host.example"], "MX": []},
}
REGISTRANT = {"acme-shop.example": "Acme Retail Group", "acme-store.example": "Acme Retail Group", "acme-outlet.example": "Redacted for Privacy",
              "acme-deals.example": "Deal Hunters LLC", "unrelated-blog.example": "Redacted for Privacy"}
IP_ORG = {"198.51.100.10": "ACME-RETAIL-DC", "198.51.100.77": "SMALL-VPS-HOSTING", "203.0.113.50": "Example CDN Edge"}
HOME = {
    "acme-shop.example": ("https://acme-shop.example/", "<script>gtag('config','G-ACME12345');</script><a href='https://acme-store.example/'>Store</a>"),
    "acme-store.example": ("https://acme-store.example/", "<script>gtag('config','G-ACME12345');</script>"),
    "acme-outlet.example": ("https://acme-shop.example/outlet", "<script>gtag('config','G-ACME12345');</script>"),
    "acme-deals.example": ("https://acme-deals.example/", "<script>gtag('config','G-DEALS9876');</script><a href='https://acme-shop.example/?aff=77'>Acme</a>"),
    "unrelated-blog.example": ("https://unrelated-blog.example/", "<p>Gardening tips</p><script>gtag('config','G-BLOG55555');</script>"),
}
DOMAINS = list(DNS)
TYPES = {"A": 1, "NS": 2, "MX": 15}


def _ev(src, payload, rel="A", cred=2):
    return Evidence(source=f"{src} [demo]", sha256=sha256_json(payload), method="demo", reliability=rel, credibility=cred, note="Synthetic demo data")


class DemoProvider:
    def crtsh(self, domain):
        rows = [c for c in CERTS if domain in c["name_value"].split("\n")]
        return rows, _ev("crt.sh Certificate Transparency", rows)

    def doh(self, name, rtype):
        payload = {"Status": 0, "Answer": [{"name": name + ".", "type": TYPES[rtype], "data": v + "."} for v in DNS.get(name, {}).get(rtype, [])]}
        return payload, _ev("Google Public DNS (DoH)", payload, "A", 1)

    def rdap_domain(self, domain):
        org = REGISTRANT.get(domain)
        if not org:
            raise FetchError("no RDAP record")
        payload = {"entities": [{"roles": ["registrant"], "vcardArray": ["vcard", [["version", {}, "text", "4.0"], ["org", {}, "text", org]]]}]}
        return payload, _ev("RDAP (domain registry)", payload)

    def rdap_ip(self, ip):
        payload = {"name": IP_ORG.get(ip, "UNKNOWN"), "entities": []}
        return payload, _ev("RDAP (IP registry)", payload)

    def homepage(self, domain):
        url, html = HOME[domain]
        ev = _ev(f"{domain} homepage", html, "B", 2)
        ev.url = url
        return url, html, ev


def load(store) -> list[dict]:
    from .collect import investigate
    p = DemoProvider()
    return [investigate(store, d, p, fetch_site=True) for d in DOMAINS]

"""FastAPI application: JSON API + graph UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .collect import import_jsonl, investigate
from .demo import DemoProvider, load
from .service import to_markdown, view
from .store import Store

STATIC = Path(__file__).parent / "static"


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="OSINT Evidence Graph", version=__version__)
    st = Store(db_path)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/workspace")
    def workspace():
        return view(st)

    @app.post("/api/investigate")
    def api_investigate(domain: str = Body(...), fetch_site: bool = Body(False)):
        d = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
        if "." not in d or " " in d:
            raise HTTPException(422, "enter a domain name such as example.com")
        provider = DemoProvider() if d.endswith(".example") else None
        return investigate(st, d, provider, fetch_site=fetch_site)

    @app.post("/api/import")
    def api_import(jsonl: str = Body(..., embed=True)):
        try:
            return {"imported": import_jsonl(st, jsonl)}
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, f"bad JSON lines: {exc}") from exc

    @app.post("/api/observations")
    def api_observe(subject_type: str = Body(...), subject: str = Body(...), predicate: str = Body(...), object_type: str = Body(...),
                    object: str = Body(...), source: str = Body(...), url: str = Body(""), reliability: str = Body("F"),
                    credibility: int = Body(6), explanation: str = Body("")):
        if not source.strip():
            raise HTTPException(422, "every observation needs a source")
        s, o = st.entity(subject_type, subject), st.entity(object_type, object)
        return {"id": st.observe(s, predicate, o, source=source, url=url, reliability=reliability, credibility=credibility,
                                 explanation=explanation, method="manual")}

    @app.delete("/api/observations/{oid}")
    def api_del(oid: str):
        st.delete_observation(oid)
        return {"ok": True}

    @app.post("/api/rules")
    def api_rule(rule: str = Body(...), lr: float = Body(...), description: str = Body("")):
        from .reasoning import RULES
        if rule not in RULES:
            raise HTTPException(404, "unknown rule")
        try:
            st.set_rule(rule, lr, description or RULES[rule]["description"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/prior")
    def api_prior(prior: float = Body(..., embed=True)):
        if not 0.001 <= prior <= 0.99:
            raise HTTPException(422, "prior must be between 0.001 and 0.99")
        st.set_setting("prior", prior)
        return {"ok": True}

    @app.post("/api/decide")
    def api_decide(hypothesis: str = Body(...), status: str = Body(...), note: str = Body(""), analyst: str = Body("")):
        h = next((x for x in view(st)["hypotheses"] if x["id"] == hypothesis), None)
        if not h:
            raise HTTPException(404, "hypothesis not found")
        try:
            st.decide(h["id"], h["kind"], h["a"], h["b"], status, note, analyst, {"posterior": h["posterior"], "factors": h["factors"]})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/demo")
    def api_demo():
        st.reset()
        return {"results": load(st)}

    @app.get("/api/report.md", response_class=PlainTextResponse)
    def report():
        return to_markdown(view(st))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()

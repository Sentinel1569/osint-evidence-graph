"use strict";
let W = null, cy = null, selH = null;
const TYPE = { domain: "#2f6fde", ip: "#2f9e6e", certificate: "#c7a14a", organization: "#7b4bc4", analytics_id: "#c2477a", nameserver: "#3fa7d6", mailserver: "#d9822b", account: "#5c7c99" };
const postColor = (p) => p >= 0.8 ? cssVar("--ok") : p >= 0.55 ? cssVar("--info") : p >= 0.2 ? cssVar("--warn") : cssVar("--muted");
const pct = (p) => p > 0.99 ? ">99%" : p < 0.01 ? "<1%" : Math.round(p * 100) + "%";
const lab = (id) => ((W.entities.find((e) => e.id === id) || {}).label || id);

async function load() { W = await api("/api/workspace"); render(); }

function render() {
  const s = W.summary;
  $("#stats").innerHTML = [["Observations", s.observations], ["Sources", s.sources], ["Conclusions", s.hypotheses], ["Likely or better", s.likely]]
    .map(([l, v]) => `<div class="card stat"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  $("#prior").value = W.prior; $("#priorV").textContent = Math.round(W.prior * 100) + "%";
  $("#rules").innerHTML = Object.entries(W.rules).map(([k, r]) => `<div class="rule" title="${esc(r.description)}"><span>${esc(k.replace(/_/g, " "))}</span><input type="number" step="0.1" min="0.05" value="${r.lr}" data-rule="${k}"></div>`).join("");
  $$("[data-rule]").forEach((x) => x.onchange = async () => { await api("/api/rules", { json: { rule: x.dataset.rule, lr: +x.value } }); await load(); });
  $("#legend").innerHTML = Object.entries(TYPE).map(([t, c]) => `<span><i style="background:${c}"></i>${t.replace("_", " ")}</span>`).join("");
  $("#hyps").innerHTML = W.hypotheses.length ? W.hypotheses.map((h) => `<div class="h ${h.id === selH ? "active" : ""}" data-h="${esc(h.id)}">
      <div class="row"><span class="post shrink" style="color:${postColor(h.posterior)}">${pct(h.posterior)}</span>
      <div><b>${esc(h.a_label)}</b> ↔ <b>${esc(h.b_label)}</b><div class="small muted">${esc(h.label)} · ${h.factors.length} factor(s) · ${h.independent_sources} source(s) · <span class="st-${h.status}">${h.status}</span></div></div></div>
      ${bar(h.posterior)}</div>`).join("") : '<div class="empty">No conclusions yet. Investigate two or more domains.</div>';
  $$("#hyps [data-h]").forEach((x) => x.onclick = () => showH(x.dataset.h));
  $("#obs").innerHTML = `<table><thead><tr><th>Subject</th><th>Predicate</th><th>Object</th><th>Source</th><th>Grade</th><th>Retrieved</th><th></th></tr></thead><tbody>
    ${W.observations.filter((o) => !hidden().has(o.subject)).map((o) => `<tr><td class="mono small">${esc(lab(o.subject))}</td><td>${esc(o.predicate)}</td><td class="mono small">${esc(lab(o.object))}</td>
    <td class="small">${o.url ? `<a href="${esc(o.url)}" target="_blank" rel="noopener noreferrer">${esc(o.source)}</a>` : esc(o.source)}<div class="muted">${esc(o.explanation)}</div></td>
    <td>${confBadge(o.confidence)} <span class="small muted">${esc(o.grade)}</span></td><td class="small mono">${esc(o.retrieved_at.slice(0, 16))}</td>
    <td><button class="ghost sm" data-del="${esc(o.id)}">✕</button></td></tr>`).join("")}</tbody></table>`;
  $$("[data-del]").forEach((x) => x.onclick = async () => { await api(`/api/observations/${x.dataset.del}`, { method: "DELETE" }); load(); });
  drawGraph();
  if (selH) showH(selH);
}

function hidden() {
  if (!$("#hideBulk").checked) return new Set();
  const bulk = new Set(W.entities.filter((e) => e.type === "certificate" && e.attrs.bulk).map((e) => e.id));
  const seeds = new Set(W.seeds.map((s) => "domain:" + s));
  const out = new Set();
  W.entities.filter((e) => e.type === "domain" && !seeds.has(e.id)).forEach((e) => {
    const os = W.observations.filter((o) => o.subject === e.id || o.object === e.id);
    if (os.length && os.every((o) => o.predicate === "has_certificate" && bulk.has(o.object))) out.add(e.id);
  });
  return out;
}

function drawGraph() {
  const hid = hidden();
  $("#hiddenN").textContent = hid.size ? `(${hid.size} hidden)` : "";
  const seeds = new Set(W.seeds.map((s) => "domain:" + s));
  const els = W.entities.filter((e) => !hid.has(e.id)).map((e) => ({ data: { id: e.id, label: e.label, type: e.type, seed: seeds.has(e.id) ? 1 : 0, weak: (e.attrs.bulk || e.attrs.shared_infra || e.attrs.big_provider || e.attrs.privacy) ? 1 : 0 } }));
  const keep = new Set(els.map((e) => e.data.id));
  W.observations.filter((o) => keep.has(o.subject) && keep.has(o.object)).forEach((o) => els.push({ data: { id: "o" + o.id, source: o.subject, target: o.object, label: o.predicate.replace(/_/g, " "), w: 1 + o.confidence * 2 } }));
  if ($("#showHyp").checked) W.hypotheses.filter((h) => h.posterior >= 0.2 || h.status !== "open").forEach((h) => els.push({ data: { id: "h" + h.id, source: h.a, target: h.b, label: pct(h.posterior), hyp: 1, color: postColor(h.posterior), w: 1 + h.posterior * 5 } }));
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("#cy"), elements: els, wheelSensitivity: 0.25,
    layout: { name: "cose", animate: false, nodeRepulsion: 60000, idealEdgeLength: 130, gravity: 0.6, padding: 24 },
    style: [
      { selector: "node", style: { "background-color": (n) => TYPE[n.data("type")] || "#999", label: "data(label)", "font-size": 10, color: cssVar("--text"), "text-valign": "bottom", "text-margin-y": 3, width: 16, height: 16, "text-max-width": 140, "text-wrap": "ellipsis" } },
      { selector: "node[seed = 1]", style: { width: 28, height: 28, "font-weight": 700, "font-size": 11 } },
      { selector: "node[weak = 1]", style: { opacity: 0.55, shape: "round-rectangle" } },
      { selector: "edge", style: { width: "data(w)", "line-color": cssVar("--border"), "curve-style": "bezier", "target-arrow-shape": "triangle", "target-arrow-color": cssVar("--border"), "arrow-scale": 0.6, label: "data(label)", "font-size": 7.5, color: cssVar("--muted"), "text-rotation": "autorotate" } },
      { selector: "edge[hyp = 1]", style: { "line-style": "dashed", "line-color": "data(color)", "target-arrow-shape": "none", color: "data(color)", "font-size": 11, "font-weight": 700, "text-background-color": cssVar("--panel-2"), "text-background-opacity": 1 } },
      { selector: ":selected", style: { "border-width": 3, "border-color": cssVar("--accent") } },
    ],
  });
  cy.on("tap", "edge[hyp = 1]", (e) => showH(e.target.id().slice(1)));
  cy.on("tap", "node", (e) => showEntity(e.target.id()));
}

function waterfall(h) {
  const rows = [{ name: "prior", p: h.prior }];
  let lo = Math.log(h.prior / (1 - h.prior));
  [...h.factors].reverse().reverse().forEach((f) => { lo += f.contribution; rows.push({ name: f.rule.replace(/_/g, " "), p: 1 / (1 + Math.exp(-lo)), d: f.contribution }); });
  const Wd = 420, rh = 22, H = rows.length * rh + 26, x = (p) => 150 + p * (Wd - 170);
  let prev = h.prior;
  const bars = rows.map((r, i) => {
    const y = 6 + i * rh;
    const a = i === 0 ? 0 : prev, b = r.p; prev = r.p;
    const col = i === 0 ? cssVar("--muted") : b >= a ? cssVar("--ok") : cssVar("--bad");
    return `<text x="0" y="${y + 14}">${esc(r.name)}</text><rect x="${x(Math.min(a, b))}" y="${y + 3}" width="${Math.max(2, Math.abs(x(b) - x(a)))}" height="14" rx="3" fill="${col}"/>
      <text x="${x(Math.max(a, b)) + 4}" y="${y + 14}">${Math.round(b * 100)}%</text>`;
  }).join("");
  return `<svg class="wf" viewBox="0 0 ${Wd} ${H}" width="100%"><line class="axis" x1="${x(0)}" x2="${x(0)}" y1="0" y2="${H - 18}"/><line class="axis" x1="${x(1)}" x2="${x(1)}" y1="0" y2="${H - 18}"/>
    <text x="${x(0) - 4}" y="${H - 4}">0%</text><text x="${x(0.5) - 10}" y="${H - 4}">50%</text><text x="${x(1) - 24}" y="${H - 4}">100%</text>${bars}</svg>`;
}

function showH(id) {
  const h = W.hypotheses.find((x) => x.id === id); if (!h) return;
  selH = id;
  $$("#hyps .h").forEach((x) => x.classList.toggle("active", x.dataset.h === id));
  const obs = Object.fromEntries(W.observations.map((o) => [o.id, o]));
  $("#detail").innerHTML = `<div style="color:var(--text)"><b>${esc(h.a_label)}</b> and <b>${esc(h.b_label)}</b> are operated by the same party?</div>
    <div class="row" style="margin:6px 0"><span class="post shrink" style="color:${postColor(h.posterior)}">${pct(h.posterior)}</span><span class="muted">${esc(h.label)} · prior ${Math.round(h.prior * 100)}% · log-odds ${h.log_odds}</span></div>
    <p style="color:var(--text)">${esc(h.explanation)}</p>${waterfall(h)}
    <h3>Evidence</h3>${h.factors.map((f) => `<div class="f"><div><b>${esc(f.rule.replace(/_/g, " "))}</b> via <span class="mono">${esc(f.via)}</span>
      <div class="muted">${esc(f.description)} LR ${f.lr} × confidence ${Math.round(f.conf * 100)}%${f.discount < 1 ? ` × ${f.discount} (repeat of the same kind)` : ""}</div>
      ${f.observations.map((oid) => obs[oid] ? `<div class="muted">↳ ${esc(lab(obs[oid].subject))} ${esc(obs[oid].predicate)} ${esc(lab(obs[oid].object))} · ${esc(obs[oid].source)} ${esc(obs[oid].grade)}</div>` : "").join("")}</div>
      <div class="mono" style="text-align:right;color:${f.contribution >= 0 ? "var(--ok)" : "var(--bad)"}">${f.contribution >= 0 ? "+" : ""}${f.contribution.toFixed(2)}</div></div>`).join("")}
    <h3>Analyst decision <span class="st-${h.status}">(${h.status})</span></h3>
    <textarea id="dNote" placeholder="What did you verify?" style="min-height:50px;font-family:inherit">${esc(h.decision.note || "")}</textarea>
    <input id="dWho" type="text" placeholder="Analyst" value="${esc(h.decision.analyst || "")}" style="margin-top:6px">
    <div class="row" style="margin-top:6px"><button class="sm" data-d="accepted">Accept</button><button class="sm ghost" data-d="rejected">Reject</button><button class="sm ghost" data-d="open">Reopen</button></div>
    ${h.decision.decided_at ? `<div class="hint">Decided ${esc(h.decision.decided_at)}; the reasoning at that moment is snapshotted in the report.</div>` : ""}`;
  $$("#detail [data-d]").forEach((b) => b.onclick = () => busy(b, async () => { await api("/api/decide", { json: { hypothesis: id, status: b.dataset.d, note: $("#dNote").value, analyst: $("#dWho").value } }); await load(); }));
}

function showEntity(id) {
  const e = W.entities.find((x) => x.id === id); if (!e) return;
  const os = W.observations.filter((o) => o.subject === id || o.object === id);
  $("#detail").innerHTML = `<div style="font-weight:650;color:var(--text)">${esc(e.label)} <span class="badge b-muted">${esc(e.type)}</span></div>
    ${Object.keys(e.attrs).length ? `<pre>${esc(JSON.stringify(e.attrs, null, 1))}</pre>` : ""}
    <h3>${os.length} observation(s)</h3>${os.slice(0, 40).map((o) => `<div class="f"><div>${esc(lab(o.subject))} <b>${esc(o.predicate)}</b> ${esc(lab(o.object))}<div class="muted">${esc(o.source)} · ${esc(o.explanation)}</div></div><div>${confBadge(o.confidence)}</div></div>`).join("")}`;
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#go").onclick = () => busy($("#go"), async () => { const r = await api("/api/investigate", { json: { domain: $("#domain").value, fetch_site: $("#fetchSite").checked } }); r.errors.forEach((e) => toast(e)); await load(); });
  $("#demo").onclick = () => busy($("#demo"), async () => { await api("/api/demo", { method: "POST" }); selH = null; await load(); });
  $("#imp").onclick = () => busy($("#imp"), async () => { const r = await api("/api/import", { json: { jsonl: $("#jsonl").value } }); toast(`Imported ${r.imported}`, "ok"); await load(); });
  $("#mAdd").onclick = () => busy($("#mAdd"), async () => {
    await api("/api/observations", { json: { subject_type: $("#mSt").value, subject: $("#mS").value, predicate: $("#mP").value, object_type: $("#mOt").value, object: $("#mO").value, source: $("#mSrc").value, url: $("#mUrl").value, reliability: $("#mRel").value, credibility: +$("#mCred").value } });
    await load();
  });
  $("#prior").oninput = () => $("#priorV").textContent = Math.round(+$("#prior").value * 100) + "%";
  $("#prior").onchange = async () => { await api("/api/prior", { json: { prior: +$("#prior").value } }); load(); };
  ["#showHyp", "#hideBulk"].forEach((s) => $(s).onchange = () => render());
  window.addEventListener("themechange", drawGraph);
  await load();
});

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


JsonObject = dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowRunNotFoundError(LookupError):
    pass


class WorkflowRunStore:
    """Persistent workflow progress used by the live developer viewer."""

    payload_inline_limit_bytes = 8 * 1024

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, name: str, metadata: JsonObject) -> JsonObject:
        run_id = f"workflow_{uuid4().hex}"
        now = utc_now()
        document = {
            "contract": "workflow.live_run",
            "schema_version": 1,
            "run_id": run_id,
            "name": name,
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "metadata": metadata,
            "steps": [],
            "summary": None,
            "error": None,
        }
        self._write(document)
        return document

    def get(self, run_id: str) -> JsonObject:

        with self._lock:
            document = self._read_document(run_id)
            self._hydrate_payloads(document)

        return document

    def _read_document(self, run_id: str) -> JsonObject:

        path = self._path(run_id)

        if not path.is_file():
            raise WorkflowRunNotFoundError(
                f"Workflow run '{run_id}' was not found."
            )

        value = json.loads(path.read_text(encoding="utf-8"))

        if not isinstance(value, dict):
            raise WorkflowRunNotFoundError(
                f"Workflow run '{run_id}' is invalid."
            )

        return value

    def start_step(
        self,
        run_id: str,
        *,
        name: str,
        title: str,
        step_input: Any,
    ) -> JsonObject:
        with self._lock, self._run_write_lock(run_id):
            document = self._read_document(run_id)
            step = {
                "step_id": f"step_{uuid4().hex}",
                "position": len(document["steps"]) + 1,
                "name": name,
                "title": title,
                "status": "running",
                "started_at": utc_now(),
                "completed_at": None,
                "input": step_input,
                "output": None,
                "error": None,
            }
            document["steps"].append(step)
            document["updated_at"] = utc_now()
            self._write(document)
            return step

    def finish_step(
        self,
        run_id: str,
        step_id: str,
        *,
        status: str,
        output: Any = None,
        error: Any = None,
    ) -> JsonObject:
        with self._lock, self._run_write_lock(run_id):
            document = self._read_document(run_id)
            step = next(
                (
                    item
                    for item in document["steps"]
                    if item.get("step_id") == step_id
                ),
                None,
            )
            if step is None:
                raise WorkflowRunNotFoundError(
                    f"Step '{step_id}' was not found."
                )
            step["status"] = status
            step["output"] = output
            step["error"] = error
            step["completed_at"] = utc_now()
            document["updated_at"] = utc_now()
            self._write(document)
            return step

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        summary: Any = None,
        error: Any = None,
    ) -> JsonObject:
        with self._lock, self._run_write_lock(run_id):
            document = self._read_document(run_id)
            document["status"] = status
            document["summary"] = summary
            document["error"] = error
            document["updated_at"] = utc_now()
            self._write(document)
            return document

    def _path(self, run_id: str) -> Path:
        if not run_id.startswith("workflow_") or not run_id.replace("_", "").isalnum():
            raise WorkflowRunNotFoundError("Invalid workflow run ID.")
        return self.root / run_id / "run.json"

    def _write(self, document: JsonObject) -> None:

        path = self._path(str(document["run_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        persisted = json.loads(
            json.dumps(document, ensure_ascii=False)
        )
        self._externalize_payloads(persisted)
        self._write_json_atomic(path, persisted)

    def _externalize_payloads(self, document: JsonObject) -> None:

        run_id = str(document["run_id"])

        for step in document.get("steps", []):

            if not isinstance(step, dict):
                continue

            step_id = str(step.get("step_id") or uuid4().hex)

            for field in ("input", "output"):
                step[field] = self._externalize_payload(
                    run_id,
                    f"{step_id}-{field}",
                    step.get(field),
                )

        document["summary"] = self._externalize_payload(
            run_id,
            "run-summary",
            document.get("summary"),
        )

    def _externalize_payload(
        self,
        run_id: str,
        payload_name: str,
        value: Any,
    ) -> Any:

        if self._is_payload_reference(value) or value is None:
            return value

        serialized = json.dumps(value, ensure_ascii=False)
        size = len(serialized.encode("utf-8"))

        if size <= self.payload_inline_limit_bytes:
            return value

        relative_path = Path("payloads") / f"{payload_name}.json"
        payload_path = self._path(run_id).parent / relative_path
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(payload_path, value)

        return {
            "contract": "workflow.payload_reference",
            "path": relative_path.as_posix(),
            "bytes": size,
        }

    def _hydrate_payloads(self, document: JsonObject) -> None:

        run_id = str(document["run_id"])

        for step in document.get("steps", []):

            if not isinstance(step, dict):
                continue

            for field in ("input", "output"):
                step[field] = self._hydrate_payload(
                    run_id,
                    step.get(field),
                )

        document["summary"] = self._hydrate_payload(
            run_id,
            document.get("summary"),
        )

    def _hydrate_payload(self, run_id: str, value: Any) -> Any:

        if not self._is_payload_reference(value):
            return value

        relative_path = Path(str(value["path"]))
        run_directory = self._path(run_id).parent.resolve()
        payload_path = (run_directory / relative_path).resolve()

        if run_directory not in payload_path.parents:
            raise WorkflowRunNotFoundError(
                f"Workflow run '{run_id}' contains an invalid payload path."
            )

        if not payload_path.is_file():
            raise WorkflowRunNotFoundError(
                f"Workflow payload '{relative_path.as_posix()}' was not found."
            )

        return json.loads(payload_path.read_text(encoding="utf-8"))

    @staticmethod
    def _is_payload_reference(value: Any) -> bool:

        return (
            isinstance(value, dict)
            and value.get("contract") == "workflow.payload_reference"
            and isinstance(value.get("path"), str)
        )

    @staticmethod
    def _write_json_atomic(path: Path, value: Any) -> None:

        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")

        try:
            temporary.write_text(
                json.dumps(value, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            for attempt in range(12):

                try:
                    temporary.replace(path)
                    break

                except PermissionError:

                    if attempt == 11:
                        raise

                    time.sleep(0.025 * (attempt + 1))

        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _run_write_lock(
        self,
        run_id: str,
        *,
        timeout_seconds: float = 15.0,
    ) -> Iterator[None]:

        run_directory = self._path(run_id).parent
        run_directory.mkdir(parents=True, exist_ok=True)
        lock_path = run_directory / ".write.lock"
        deadline = time.monotonic() + timeout_seconds
        descriptor: int | None = None

        while descriptor is None:

            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )

            except (FileExistsError, PermissionError):

                if not lock_path.exists():
                    raise

                try:
                    lock_age = time.time() - lock_path.stat().st_mtime

                    if lock_age > 30.0:
                        lock_path.unlink()
                        continue

                except FileNotFoundError:
                    continue

                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting to update workflow run '{run_id}'."
                    )

                time.sleep(0.02)

        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            yield

        finally:
            os.close(descriptor)
            lock_path.unlink(missing_ok=True)


def workflow_viewer_html(run_id: str) -> str:
    safe_run_id = json.dumps(run_id)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Workflow Live View</title>
<style>
:root{{--bg:#09111f;--panel:#111d30;--panel2:#16243a;--line:#2b3b55;--text:#eaf1fb;--muted:#94a7c2;--blue:#62a9ff;--green:#45d19a;--amber:#ffc861;--red:#ff7383}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 15% 0,#172b48 0,#09111f 42%);color:var(--text);font:15px/1.55 Inter,Segoe UI,sans-serif}}
main{{max-width:1520px;margin:auto;padding:42px 32px 100px}} .hero{{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:28px}}
.eyebrow{{color:var(--blue);font-size:12px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}} h1{{font-size:34px;margin:5px 0 7px}} .muted{{color:var(--muted)}}
.badge{{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:999px;padding:8px 13px;background:#0d1829;font-weight:700}}
.dot{{width:9px;height:9px;border-radius:50%;background:var(--amber);box-shadow:0 0 0 5px #ffc86118}} .running .dot{{animation:pulse 1.3s infinite}} .completed .dot{{background:var(--green)}} .failed .dot{{background:var(--red)}}
@keyframes pulse{{50%{{opacity:.35}}}} .overview{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}} .metric{{padding:15px 17px;background:#0e192a;border:1px solid var(--line);border-radius:12px}} .metric b{{display:block;font-size:19px}} .metric span{{color:var(--muted);font-size:12px}}
.toolbar{{display:flex;justify-content:flex-end;gap:10px;margin:-8px 0 18px}} button{{border:1px solid var(--line);background:#101d30;color:var(--text);padding:9px 14px;border-radius:9px;font-weight:700;cursor:pointer}} button:hover{{border-color:var(--blue);background:#142641}}
.agent-overview{{display:none;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px;margin:0 0 24px}} .agent-panel{{background:linear-gradient(145deg,#13233a,#0d1828);border:1px solid var(--line);border-radius:16px;padding:22px;min-width:0}} .agent-panel h2{{font-size:19px;margin:0 0 5px}} .agent-panel .sub{{color:var(--muted);font-size:13px;margin-bottom:18px}} .knowledge-summary{{font-size:16px;line-height:1.65;padding:14px;background:#091321;border-radius:10px;margin-bottom:16px}} .knowledge-groups{{display:grid;gap:14px}} .knowledge-group h4{{margin:0 0 7px;color:#91b4e3}} .knowledge-group ul{{margin:0;padding-left:21px;display:grid;gap:5px}}
.coverage-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px}} .coverage-chip{{background:#091321;border:1px solid #22324a;border-radius:10px;padding:10px}} .coverage-chip b{{display:block;font-size:18px;color:var(--blue)}} .coverage-chip span{{color:var(--muted);font-size:11px}} .graph-nodes{{display:grid;gap:9px;margin-bottom:14px}} .graph-node{{border:1px solid #2c405d;background:#0b1626;border-radius:11px;padding:12px}} .graph-node.current{{border-color:var(--blue);box-shadow:0 0 0 2px #62a9ff18}} .graph-node-head{{display:flex;justify-content:space-between;gap:10px;font-weight:800}} .graph-node p{{margin:6px 0 0;color:var(--muted)}} .graph-node small{{color:#87a4c9}} .graph-edges{{display:grid;gap:7px}} .graph-edge{{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:9px;background:#091321;border-radius:9px;padding:9px 11px}} .graph-edge .arrow{{color:var(--blue);text-align:center;overflow-wrap:anywhere}} .graph-edge small{{color:var(--muted)}}
.steps{{display:grid;gap:16px}} .step{{background:linear-gradient(145deg,var(--panel),#0d1828);border:1px solid var(--line);border-radius:16px;overflow:hidden}} .step.active{{border-color:var(--blue);box-shadow:0 0 0 3px #62a9ff12}}
.step-head{{display:flex;align-items:center;gap:14px;padding:18px 22px;cursor:pointer}} .step-head:hover{{background:#ffffff04}} .num{{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:var(--panel2);color:var(--blue);font-weight:800}} .step-title{{flex:1}} .step-title b{{display:block;font-size:17px}} .status{{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}} .status.completed{{color:var(--green)}} .status.running{{color:var(--amber)}} .status.failed{{color:var(--red)}}
.step-body{{border-top:1px solid var(--line);padding:26px 22px 28px;display:none;grid-template-columns:minmax(0,1fr);gap:28px}} .step.open .step-body{{display:grid}} .section{{min-width:0}} .section h3{{font-size:13px;letter-spacing:.11em;text-transform:uppercase;color:#a9c8f4;margin:0 0 11px}} .box{{background:#091321;border:1px solid #22324a;border-radius:12px;padding:20px;overflow:visible}}
.fields{{display:grid;gap:10px}} .field-row{{display:grid;grid-template-columns:190px minmax(0,1fr);gap:14px;padding:4px 0;align-items:start}} .field-row+.field-row{{border-top:1px solid #ffffff08;padding-top:13px}} .field-row.wide{{grid-template-columns:minmax(0,1fr);gap:8px}} .key{{color:#91b4e3;font-weight:750}} .field-value{{min-width:0}} .value{{min-width:0;overflow-wrap:anywhere}} .text{{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font:14px/1.7 Consolas,monospace;color:#dce8f8;background:#07101c;border-radius:9px;padding:15px}} .list{{margin:0;padding-left:24px;display:grid;gap:7px}} .empty{{color:var(--muted);font-style:italic}}
.error{{border-color:#63313c;background:#24131b;color:#ffdce1}} footer{{margin-top:24px;color:var(--muted);font-size:12px}} @media(max-width:900px){{.agent-overview{{grid-template-columns:1fr}}}} @media(max-width:760px){{main{{padding:24px 14px 60px}}.overview{{grid-template-columns:1fr}}.hero{{display:block}}.badge{{margin-top:14px}}.field-row{{grid-template-columns:1fr;gap:5px}}.toolbar{{justify-content:stretch}}button{{flex:1}}.coverage-row{{grid-template-columns:1fr 1fr}}}}
</style>
</head>
<body><main>
<div class="hero"><div><div class="eyebrow">Developer workflow monitor</div><h1 id="title">Loading workflow…</h1><div class="muted" id="runId"></div></div><div class="badge running" id="runBadge"><span class="dot"></span><span id="runStatus">Connecting</span></div></div>
<div class="overview"><div class="metric"><b id="progress">—</b><span>steps completed</span></div><div class="metric"><b id="current">—</b><span>current step</span></div><div class="metric"><b id="updated">—</b><span>last update</span></div></div>
<div class="toolbar"><button id="expandAll" type="button">Expand all steps</button><button id="collapseAll" type="button">Collapse all steps</button></div>
<section class="agent-overview" id="agentOverview"><article class="agent-panel"><h2>Agent knowledge box</h2><div class="sub">Persistent understanding accumulated from observed behavior</div><div id="knowledgeContent"></div></article><article class="agent-panel"><h2>Exploration graph</h2><div class="sub">Screens are nodes; executed actions are directed transitions</div><div id="graphContent"></div></article></section>
<section class="steps" id="steps"></section><footer>This page updates automatically while the workflow runs. Sensitive authentication values are never recorded.</footer>
</main>
<script>
const runId={safe_run_id}; const endpoint=`/api/v1/workflow-runs/${{runId}}`;
const label=s=>String(s).replaceAll("_"," ").replace(/\\b\\w/g,c=>c.toUpperCase());
function duration(step){{if(!step.completed_at)return"running now";const ms=new Date(step.completed_at)-new Date(step.started_at);return ms<1000?`${{ms}} ms`:`${{(ms/1000).toFixed(1)}} s`}}
function valueNode(value){{
 const wrap=document.createElement("div");
 if(value===null||value===undefined||value===""){{wrap.className="empty";wrap.textContent="None";return wrap}}
 if(typeof value==="string"){{wrap.className=value.includes("\\n")||value.length>120?"text":"value";wrap.textContent=value;return wrap}}
 if(typeof value!=="object"){{wrap.className="value";wrap.textContent=String(value);return wrap}}
 if(Array.isArray(value)){{if(!value.length){{wrap.className="empty";wrap.textContent="None";return wrap}} const ul=document.createElement("ul");ul.className="list";value.forEach(v=>{{const li=document.createElement("li");li.append(valueNode(v));ul.append(li)}});return ul}}
 wrap.className="fields"; Object.entries(value).forEach(([k,v])=>{{const row=document.createElement("div");const complex=(typeof v==="object"&&v!==null)||(typeof v==="string"&&(v.includes("\\n")||v.length>120));row.className="field-row"+(complex?" wide":"");const key=document.createElement("div");key.className="key";key.textContent=label(k);const field=document.createElement("div");field.className="field-value";field.append(valueNode(v));row.append(key,field);wrap.append(row)}}); return wrap
}}
function section(title,value,error=false){{const s=document.createElement("div");s.className="section";const h=document.createElement("h3");h.textContent=title;const b=document.createElement("div");b.className="box"+(error?" error":"");b.append(valueNode(value));s.append(h,b);return s}}
function knowledgeGroup(title,items){{if(!Array.isArray(items)||!items.length)return null;const group=document.createElement("div");group.className="knowledge-group";const h=document.createElement("h4");h.textContent=title;const ul=document.createElement("ul");items.forEach(item=>{{const li=document.createElement("li");li.textContent=typeof item==="string"?item:(item.name||label(JSON.stringify(item)));ul.append(li)}});group.append(h,ul);return group}}
function renderAgentState(run){{const memory=[...run.steps].reverse().find(step=>step.output&&step.output.graph&&step.output.knowledge_box);const panel=document.getElementById("agentOverview");if(!memory){{panel.style.display="none";return}}panel.style.display="grid";const knowledge=memory.output.knowledge_box;const kc=document.getElementById("knowledgeContent");kc.replaceChildren();const summary=document.createElement("div");summary.className="knowledge-summary";summary.textContent=knowledge.app_summary||"The agent is still building its overall understanding.";kc.append(summary);const groups=document.createElement("div");groups.className="knowledge-groups";[["Features",knowledge.features],["Confirmed facts",knowledge.facts],["Hypotheses to verify",knowledge.hypotheses],["Discovered workflows",knowledge.workflows],["Failures and crashes",knowledge.failures],["Open questions",knowledge.open_questions],["Agent notes",knowledge.notes]].forEach(([name,items])=>{{const group=knowledgeGroup(name,items);if(group)groups.append(group)}});kc.append(groups);
 const graph=memory.output.graph;const gc=document.getElementById("graphContent");gc.replaceChildren();const coverage=document.createElement("div");coverage.className="coverage-row";Object.entries(graph.coverage||{{}}).forEach(([key,val])=>{{const chip=document.createElement("div");chip.className="coverage-chip";const b=document.createElement("b");b.textContent=val;const s=document.createElement("span");s.textContent=label(key);chip.append(b,s);coverage.append(chip)}});gc.append(coverage);const nodes=document.createElement("div");nodes.className="graph-nodes";(graph.nodes||[]).forEach(node=>{{const card=document.createElement("div");card.className="graph-node"+(node.node_id===graph.current_node_id?" current":"");const head=document.createElement("div");head.className="graph-node-head";const id=document.createElement("span");id.textContent=node.node_id;const actions=document.createElement("small");actions.textContent=`${{(node.actions||[]).filter(a=>a.status==="explored").length}}/${{(node.actions||[]).length}} actions`;head.append(id,actions);const meta=document.createElement("small");meta.textContent=[node.package,node.activity].filter(Boolean).join(" · ");const p=document.createElement("p");p.textContent=node.summary||node.purpose||"Awaiting agent understanding";card.append(head,meta,p);nodes.append(card)}});gc.append(nodes);const edges=document.createElement("div");edges.className="graph-edges";(graph.edges||[]).slice(-12).forEach(edge=>{{const row=document.createElement("div");row.className="graph-edge";const from=document.createElement("b");from.textContent=edge.from_node_id;const arrow=document.createElement("div");arrow.className="arrow";arrow.textContent=`— ${{edge.action_key}} →`;const to=document.createElement("div");const target=document.createElement("b");target.textContent=edge.to_node_id;const outcome=document.createElement("small");outcome.textContent=edge.classification||"";to.append(target,document.createElement("br"),outcome);row.append(from,arrow,to);edges.append(row)}});gc.append(edges)}}
function render(run){{
 document.title=`${{run.name}} · Workflow Live View`;document.getElementById("title").textContent=label(run.name);document.getElementById("runId").textContent=run.run_id;
 const badge=document.getElementById("runBadge");badge.className=`badge ${{run.status}}`;document.getElementById("runStatus").textContent=label(run.status);
 const done=run.steps.filter(s=>s.status==="completed"||s.status==="skipped").length;document.getElementById("progress").textContent=`${{done}} / ${{run.steps.length}}`;
 const active=[...run.steps].reverse().find(s=>s.status==="running");document.getElementById("current").textContent=active?active.title:(run.status==="running"?"Starting…":"Finished");
 document.getElementById("updated").textContent=new Date(run.updated_at).toLocaleTimeString();
 renderAgentState(run);const root=document.getElementById("steps");root.replaceChildren();const latestLlm=[...run.steps].reverse().find(step=>step.name==="llm_decision");
 run.steps.forEach(step=>{{const card=document.createElement("article");const opened=step.status==="running"||step.status==="failed"||(latestLlm&&step.step_id===latestLlm.step_id);card.className="step"+(step.status==="running"?" active":"")+(opened?" open":"");const head=document.createElement("div");head.className="step-head";const n=document.createElement("span");n.className="num";n.textContent=step.position;const title=document.createElement("div");title.className="step-title";title.innerHTML=`<b></b><span class="muted"></span>`;title.querySelector("b").textContent=step.title;title.querySelector("span").textContent=`${{label(step.name)}} · ${{duration(step)}}`;const st=document.createElement("span");st.className=`status ${{step.status}}`;st.textContent=label(step.status);head.append(n,title,st);head.addEventListener("click",()=>card.classList.toggle("open"));card.append(head);
 const body=document.createElement("div");body.className="step-body";let left=step.input;let right=step.error||step.output;if(step.name==="llm_decision"&&step.output){{const exchanges=step.output.provider_exchanges||[];left={{workflow_input:step.input,provider_requests:exchanges.map(x=>x.request)}};right={{parsed_decision:step.output.decision,provider_responses:exchanges.map(x=>x.response)}}}}body.append(section(step.name==="llm_decision"?"LLM request / input":"Input",left));body.append(section(step.name==="llm_decision"?"LLM response / output":"Output",right,!!step.error));card.append(body);root.append(card)}});
 if(run.error)root.append(section("Workflow error",run.error,true));
}}
async function refresh(){{try{{const r=await fetch(endpoint,{{cache:"no-store"}});if(!r.ok)throw new Error(`HTTP ${{r.status}}`);const body=await r.json();render(body.run);if(body.run.status==="running")setTimeout(refresh,700)}}catch(e){{document.getElementById("current").textContent="Connection error";setTimeout(refresh,1500)}}}}
refresh();
document.getElementById("expandAll").addEventListener("click",()=>document.querySelectorAll(".step").forEach(step=>step.classList.add("open")));
document.getElementById("collapseAll").addEventListener("click",()=>document.querySelectorAll(".step").forEach(step=>step.classList.remove("open")));
</script></body></html>"""

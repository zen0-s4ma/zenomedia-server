#!/usr/bin/env python3
import os, html
from collections import deque
from xmlrpc.client import ServerProxy, Fault, ProtocolError
from fastapi import FastAPI, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

APP_TITLE = "Py Runner"
security = HTTPBasic()

SUP_USER = os.environ.get("SUPERVISOR_USER", "admin")
SUP_PASS = os.environ.get("SUPERVISOR_PASSWORD", "admin")
SUP_URL  = os.environ.get("SUPERVISOR_URL", "http://127.0.0.1:9001/RPC2")
LOGS_DIR = os.environ.get("LOGS_DIR", "/workspace/logs")

def get_rpc():
    from urllib.parse import urlparse, urlunparse
    u = urlparse(SUP_URL)
    netloc = f"{SUP_USER}:{SUP_PASS}@{u.hostname}:{u.port or 9001}"
    return ServerProxy(urlunparse((u.scheme, netloc, u.path or "/RPC2", "", "", "")), allow_none=True)

def auth(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username == SUP_USER and credentials.password == SUP_PASS:
        return True
    raise HTTPException(status_code=401, detail="Unauthorized")

def badge(state: str) -> str:
    colors = {
        "RUNNING":"bg-green-100 text-green-800",
        "STOPPED":"bg-gray-200 text-gray-800",
        "STARTING":"bg-blue-100 text-blue-800",
        "STOPPING":"bg-yellow-100 text-yellow-800",
        "EXITED":"bg-red-100 text-red-800",
        "FATAL":"bg-red-200 text-red-900",
        "BACKOFF":"bg-orange-100 text-orange-800",
        "UNKNOWN":"bg-gray-100 text-gray-700"
    }
    return f'<span class="px-2 py-0.5 rounded text-xs {colors.get(state, colors["UNKNOWN"])}">{state}</span>'

def tail_file(path: str, max_lines: int = 200) -> str:
    if not os.path.exists(path): return "(archivo no existe)"
    dq = deque(maxlen=max_lines)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f: dq.append(line.rstrip("\n"))
    return "\n".join(dq)

app = FastAPI(title=APP_TITLE)

HTML_HEAD = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Py Runner</title>
<script src="https://cdn.tailwindcss.com"></script>
</head><body class="bg-slate-50 text-slate-900">
<div class="max-w-6xl mx-auto p-6">
<h1 class="text-2xl font-semibold mb-4">Python Scripts <span class="text-sm text-slate-500">(UI)</span></h1>
<div class="mb-4 text-sm text-slate-600">Backend: Supervisor RPC • Logs en <code>{html.escape(LOGS_DIR)}</code></div>
"""
HTML_FOOT = """</div></body></html>"""

@app.get("/", response_class=HTMLResponse)
def home(_: bool = Depends(auth)):
    rpc = get_rpc()
    try:
        procs = rpc.supervisor.getAllProcessInfo()
    except (Fault, ProtocolError) as e:
        raise HTTPException(500, f"Supervisor RPC error: {e}")

    def is_user_proc(p): return p["name"] not in ("cron", "py-autoreg", "py-ui")
    procs = sorted(filter(is_user_proc, procs), key=lambda p: p["name"].lower())

    rows = []
    for p in procs:
        name = p["name"]; state = p["statename"]
        desc = html.escape(p.get("description",""))
        rows.append(f"""
<tr class="hover:bg-slate-50">
  <td class="py-2 px-3 font-medium">{html.escape(name)}</td>
  <td class="py-2 px-3">{badge(state)}</td>
  <td class="py-2 px-3 text-xs text-slate-500">{desc}</td>
  <td class="py-2 px-3 flex gap-2">
    <form method="post" action="/action/start"><input type="hidden" name="name" value="{html.escape(name)}">
      <button class="px-3 py-1 rounded bg-emerald-600 text-white disabled:opacity-40" {"disabled" if state=="RUNNING" else ""}>Start</button>
    </form>
    <form method="post" action="/action/stop"><input type="hidden" name="name" value="{html.escape(name)}">
      <button class="px-3 py-1 rounded bg-rose-600 text-white disabled:opacity-40" {"disabled" if state!="RUNNING" else ""}>Stop</button>
    </form>
    <form method="post" action="/action/restart"><input type="hidden" name="name" value="{html.escape(name)}">
      <button class="px-3 py-1 rounded bg-indigo-600 text-white">Restart</button>
    </form>
    <a class="px-3 py-1 rounded bg-slate-200" href="/logs/{html.escape(name)}?stream=stdout">Logs</a>
  </td>
</tr>""")

    table = f"""
<div class="bg-white shadow-sm rounded-lg overflow-hidden">
<table class="min-w-full text-sm">
  <thead class="bg-slate-100 text-slate-700">
    <tr><th class="py-2 px-3 text-left">Script</th><th class="py-2 px-3 text-left">Estado</th><th class="py-2 px-3 text-left">Descripción</th><th class="py-2 px-3 text-left">Acciones</th></tr>
  </thead>
  <tbody>{"".join(rows) if rows else '<tr><td class="p-4 text-slate-500" colspan="4">No hay scripts registrados aún.</td></tr>'}</tbody>
</table>
</div>
<div class="mt-4 text-right">
  <form method="post" action="/refresh">
    <button class="px-3 py-1 rounded bg-slate-800 text-white">Refrescar</button>
  </form>
</div>
"""
    return HTML_HEAD + table + HTML_FOOT

@app.post("/refresh")
def refresh(_: bool = Depends(auth)):
    return RedirectResponse("/", status_code=303)

def _rpc_action(name: str, op: str):
    rpc = get_rpc()
    try:
        if op == "start":   rpc.supervisor.startProcess(name)
        elif op == "stop":  rpc.supervisor.stopProcess(name)
        elif op == "restart":
            try: rpc.supervisor.stopProcess(name)
            finally: rpc.supervisor.startProcess(name)
        else: raise HTTPException(400, "Operación no válida")
    except Fault as e:
        raise HTTPException(400, f"RPC error: {e.faultString}")

@app.post("/action/start")
def do_start(name: str = Form(...), _: bool = Depends(auth)):
    _rpc_action(name, "start"); return RedirectResponse("/", status_code=303)

@app.post("/action/stop")
def do_stop(name: str = Form(...), _: bool = Depends(auth)):
    _rpc_action(name, "stop"); return RedirectResponse("/", status_code=303)

@app.post("/action/restart")
def do_restart(name: str = Form(...), _: bool = Depends(auth)):
    _rpc_action(name, "restart"); return RedirectResponse("/", status_code=303)

@app.get("/logs/{name}", response_class=HTMLResponse)
def show_logs(name: str, stream: str = "stdout", _: bool = Depends(auth)):
    stream = "stderr" if stream.lower()=="stderr" else "stdout"
    path = os.path.join(LOGS_DIR, f"{name}.{ 'err' if stream=='stderr' else 'out' }.log")
    content = tail_file(path, 400)
    html_log = html.escape(content)
    switch = "stderr" if stream=="stdout" else "stdout"
    switch_url = f"/logs/{name}?stream={switch}"
    body = f"""
<div class="bg-white shadow-sm rounded-lg p-4">
  <div class="flex items-center justify-between">
    <h2 class="text-lg font-semibold">Logs: {html.escape(name)} <span class="text-slate-500 text-sm">({stream})</span></h2>
    <div class="space-x-2">
      <a class="px-3 py-1 rounded bg-slate-200" href="{switch_url}">Ver {switch}</a>
      <a class="px-3 py-1 rounded bg-slate-800 text-white" href="/">Volver</a>
    </div>
  </div>
  <pre class="mt-3 text-xs bg-slate-950 text-slate-100 p-4 rounded overflow-auto max-h-[70vh]">{html_log}</pre>
</div>
"""
    return HTML_HEAD + body + HTML_FOOT

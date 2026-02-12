#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import threading
import queue
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# --- Windows key input (scroll interno + prompt no bloqueante) ---
try:
    import msvcrt  # type: ignore

    HAS_MSVCRT = True
except Exception:
    HAS_MSVCRT = False

console = Console()


# =========================
# Helpers
# =========================
def _clamp(v: int, lo: int, hi: int) -> int:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def ts_compact() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def which_or_die(bin_name: str) -> str:
    p = shutil.which(bin_name)
    if not p:
        console.print(f"[bold red]ERROR:[/] No encuentro '{bin_name}' en PATH.")
        raise SystemExit(2)
    return p


def human_dt(seconds: float) -> str:
    s = int(seconds)
    m, ss = divmod(s, 60)
    h, mm = divmod(m, 60)
    return f"{h}:{mm:02d}:{ss:02d}" if h else f"{mm}:{ss:02d}"


def app_base_dir() -> Path:
    base = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    return base.parent


def read_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def find_config(cli_path: Optional[str]) -> Tuple[Path, Dict[str, Any]]:
    candidates: List[Path] = []

    if cli_path:
        candidates.append(Path(cli_path))

    envp = os.environ.get("ZENOVERSO_CONFIG")
    if envp:
        candidates.append(Path(envp))

    candidates.append(app_base_dir() / "zenoverso.yml")
    candidates.append(Path(r"D:\Github-zen0s4ma\zenomedia-server\_zenoverso_cli\zenoverso.yml"))

    for p in candidates:
        if p.exists():
            return p, read_yaml(p)

    console.print("[bold red]ERROR:[/] No encuentro zenoverso.yml. He buscado en:")
    for p in candidates:
        console.print(f"  - {p}")
    raise SystemExit(2)


def parse_selection(raw: str) -> List[int]:
    raw = raw.strip().lower()
    if not raw:
        return []
    tokens = re.split(r"[,\s]+", raw)
    out: List[int] = []
    for t in tokens:
        if not t:
            continue
        if re.fullmatch(r"\d+-\d+", t):
            a, b = t.split("-", 1)
            ia, ib = int(a), int(b)
            step = 1 if ib >= ia else -1
            out.extend(list(range(ia, ib + step, step)))
        elif re.fullmatch(r"\d+", t):
            out.append(int(t))
        else:
            raise ValueError(f"Token inválido: {t}")
    return out

def dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def win_to_wsl_path(p: Path) -> str:
    s = str(p)
    m = re.match(r"^([A-Za-z]):\\(.*)$", s)
    if not m:
        return s.replace("\\", "/")
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def choose_services_multi(services: List[str]) -> List[str]:
    if not services:
        console.print("[bold red]No pude leer servicios del compose.[/]")
        return []

    table = Table(title="Servicios disponibles (para pull)", box=box.SIMPLE_HEAVY, show_lines=True)
    table.add_column("#", style="bold yellow", width=4, justify="right")
    table.add_column("Servicio", style="bold")
    for i, s in enumerate(services, start=1):
        table.add_row(str(i), s)

    console.print(Panel(table, box=box.ROUNDED))
    raw = console.input("[bold cyan]Selecciona[/] (ej: 1,3,7 o 2-5): ").strip()
    if not raw:
        return []

    idxs = parse_selection(raw)
    picked: List[str] = []
    for n in idxs:
        if 1 <= n <= len(services):
            picked.append(services[n - 1])

    return dedupe_keep_order(picked)

def load_compose_services(compose_file: Path) -> List[str]:
    if not compose_file.exists():
        return []
    try:
        data = yaml.safe_load(compose_file.read_text(encoding="utf-8", errors="ignore")) or {}
        services = (data.get("services") or {}).keys()
        return sorted(list(services))
    except Exception:
        return []


# =========================
# Historial (NDJSON)
# =========================
def load_history(history_file: Path, keep_max: int) -> List[Dict[str, Any]]:
    if not history_file.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        lines = history_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        for ln in lines[-max(1, keep_max) :]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                entries.append(json.loads(ln))
            except Exception:
                continue
    except Exception:
        return []
    return entries


def append_history(history_file: Path, entry: Dict[str, Any]) -> None:
    safe_mkdir(history_file.parent)
    with history_file.open("a", encoding="utf-8", errors="ignore") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def build_history_panel(history: List[Dict[str, Any]], show_last: int, history_file: Path) -> Panel:
    show_last = max(0, int(show_last))
    if show_last == 0:
        return Panel(f"[dim]Historial en: {history_file}[/]", title="Historial", box=box.ROUNDED)

    if not history:
        return Panel(
            f"[dim]Aún no hay ejecuciones registradas.[/]\n\n[dim]Historial en: {history_file}[/]",
            title="Historial",
            box=box.ROUNDED,
        )

    table = Table(box=box.SIMPLE_HEAVY, show_lines=True)
    table.add_column("Inicio", style="dim", width=19)
    table.add_column("ID", style="bold yellow", width=4, justify="right")
    table.add_column("Comando", style="bold")
    table.add_column("Resultado", width=9)
    table.add_column("Tiempo", justify="right", width=8)
    table.add_column("Log(s)", overflow="fold")

    for e in reversed(history[-show_last:]):
        start = str(e.get("start", ""))
        start_short = start
        if "T" in start and len(start) >= 19:
            date_part = start.split("T", 1)[0]
            time_part = start.split("T", 1)[1][:8]
            start_short = f"{date_part} {time_part}"

        cid = str(e.get("cmd_id", ""))
        title = str(e.get("cmd_title", ""))

        ok = bool(e.get("ok", False))
        result = "[green]OK[/]" if ok else "[red]FAIL[/]"

        dur = str(e.get("duration_human", ""))

        logs = e.get("logs", [])
        if isinstance(logs, list) and logs:
            last_log = str(logs[-1].get("log_file", "")) if isinstance(logs[-1], dict) else str(logs[-1])
            extra = len(logs) - 1
            log_txt = last_log + (f"\n[dim](+{extra})[/]" if extra > 0 else "")
        else:
            log_txt = "[dim]-[/]"

        table.add_row(start_short, cid, title, result, dur, log_txt)

    return Panel(
        table,
        title="Historial",
        subtitle=f"Guardado en: {history_file}",
        box=box.ROUNDED,
    )


# =========================
# Models
# =========================
@dataclass
class Action:
    type: str                        # shell | compose | compose_logs | docker_exec | compose_exec | compose_pull_select
    cmd: Optional[str] = None        # shell
    subcmd: Optional[str] = None     # compose
    service: Optional[str] = None    # compose_logs / compose_exec
    container: Optional[str] = None  # docker_exec
    exec_cmd: Optional[str] = None   # docker_exec / compose_exec
    tty: bool = False
    stdin: bool = False
    mode: str = "live"               # live | passthrough
    tail_lines: int = 60             # altura visible
    follow: bool = False
    tail: int = 200                  # logs tail


@dataclass
class CommandDef:
    id: int
    title: str
    desc: str
    actions: List[Action]


# =========================
# UI (home)
# =========================
def render_header(app_name: str, author: str, cfg_path: Path) -> Panel:
    title = Text(app_name, style="bold cyan")
    subtitle = Text(f"Creado por {author}  ·  Config: {cfg_path}", style="dim")
    return Panel.fit(Text.assemble(title, "\n", subtitle), box=box.ROUNDED)


def build_menu_table(commands: List[CommandDef]) -> Table:
    table = Table(title="Comandos disponibles", box=box.SIMPLE_HEAVY, show_lines=True)
    table.add_column("ID", style="bold yellow", width=4, justify="right")
    table.add_column("Comando", style="bold")
    table.add_column("Descripción")
    for c in commands:
        table.add_row(str(c.id), c.title, c.desc)
    return table


def build_shortcuts_panel() -> Panel:
    return Panel(
        "[bold]Uso:[/]\n"
        "• IDs en orden: [cyan]1,3,7[/] o [cyan]1 3 7[/]\n"
        "• Rangos: [cyan]2-5[/]\n"
        "• [cyan]r[/] recargar config · [cyan]q[/] salir\n"
        "• (en ejecución) scroll: ↑/↓ PgUp/PgDn Home/End",
        title="Atajos",
        box=box.ROUNDED,
    )


def render_home_group(
    app_name: str,
    author: str,
    cfg_path: Path,
    commands: List[CommandDef],
    history_cache: List[Dict[str, Any]],
    history_show_last: int,
    history_file: Path,
    input_buf: str,
) -> Group:
    prompt = Text.assemble(
        Text("Selecciona", style="bold cyan"),
        Text(" (IDs / r / q): ", style="dim"),
        Text(input_buf, style="bold"),
    )
    return Group(
        render_header(app_name, author, cfg_path),
        build_menu_table(commands),
        build_shortcuts_panel(),
        build_history_panel(history_cache, history_show_last, history_file),
        prompt,
    )


def ask_line_home(
    app_name: str,
    author: str,
    cfg_path: Path,
    commands: List[CommandDef],
    history_cache: List[Dict[str, Any]],
    history_show_last: int,
    history_file: Path,
    clear_screen: bool,
) -> str:
    if not HAS_MSVCRT:
        if clear_screen:
            console.clear()
        console.print(render_home_group(app_name, author, cfg_path, commands, history_cache, history_show_last, history_file, ""))
        return input("> ").strip()

    if clear_screen:
        console.clear()

    buf = ""
    last_size = console.size

    with Live(
        render_home_group(app_name, author, cfg_path, commands, history_cache, history_show_last, history_file, buf),
        console=console,
        auto_refresh=False,
    ) as live:
        live.refresh()

        while True:
            sz = console.size
            if sz != last_size:
                live.update(render_home_group(app_name, author, cfg_path, commands, history_cache, history_show_last, history_file, buf))
                live.refresh()
                last_size = sz

            if msvcrt.kbhit():  # type: ignore[name-defined]
                ch = msvcrt.getwch()  # type: ignore[name-defined]

                if ch == "\x03":
                    raise KeyboardInterrupt

                if ch in ("\r", "\n"):
                    return buf

                if ch == "\b":
                    buf = buf[:-1]
                elif ch in ("\x00", "\xe0"):
                    _ = msvcrt.getwch()  # type: ignore[name-defined]
                else:
                    if ch.isprintable():
                        buf += ch

                live.update(render_home_group(app_name, author, cfg_path, commands, history_cache, history_show_last, history_file, buf))
                live.refresh()
            else:
                time.sleep(0.03)


def ask_text(title: str, body: str, default: str = "") -> str:
    """Mini prompt no-bloqueante (msvcrt) dentro de alt-screen."""
    if not HAS_MSVCRT:
        console.print(Panel(body, title=title, box=box.ROUNDED))
        val = input(f"{title} [{default}]: ").strip()
        return val if val else default

    buf = default
    last_size = console.size

    def render() -> Group:
        prompt = Text.assemble(
            Text(title, style="bold cyan"),
            Text(" : ", style="dim"),
            Text(buf, style="bold"),
        )
        return Group(Panel(body, title=title, box=box.ROUNDED), prompt)

    with Live(render(), console=console, auto_refresh=False) as live:
        live.refresh()
        while True:
            sz = console.size
            if sz != last_size:
                live.update(render())
                live.refresh()
                last_size = sz

            if msvcrt.kbhit():  # type: ignore[name-defined]
                ch = msvcrt.getwch()  # type: ignore[name-defined]

                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in ("\r", "\n"):
                    return buf.strip()
                if ch == "\b":
                    buf = buf[:-1]
                elif ch in ("\x00", "\xe0"):
                    _ = msvcrt.getwch()  # type: ignore[name-defined]
                else:
                    if ch.isprintable():
                        buf += ch

                live.update(render())
                live.refresh()
            else:
                time.sleep(0.03)


def choose_service_from_list(services: List[str]) -> str:
    if not services:
        return ask_text("Servicio", "No pude leer servicios del compose. Escribe el nombre exacto:", default="").strip()

    body_lines = []
    for i, s in enumerate(services, start=1):
        body_lines.append(f"[bold yellow]{i:>2}[/]  {s}")
    body = "\n".join(body_lines) + "\n\nEscribe número o nombre exacto."

    val = ask_text("Servicio", body, default="1").strip()
    if not val:
        return services[0]
    if val.isdigit():
        idx = int(val)
        if 1 <= idx <= len(services):
            return services[idx - 1]
        return services[0]
    if val in services:
        return val
    return services[0]


# =========================
# Runner (rápido, sin bloquear pipes)
# =========================
class _DropQueue:
    """Queue que NUNCA bloquea al reader: si se llena, tira lo viejo (UI) pero el LOG queda completo."""
    def __init__(self, max_items: int = 200) -> None:
        self.q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=max_items)

    def put(self, item: Optional[bytes]) -> None:
        while True:
            try:
                self.q.put_nowait(item)
                return
            except queue.Full:
                try:
                    _ = self.q.get_nowait()
                except queue.Empty:
                    return

    def get_nowait(self) -> Optional[bytes]:
        return self.q.get_nowait()


def run_live_command(
    cmd: List[str],
    title: str,
    log_file: Path,
    cwd: Optional[Path] = None,
    view_height: int = 60,
    buffer_max: int = 5000,
    tail_lines: Optional[int] = None,   # compat: por si lo llamas así
) -> int:
    if tail_lines is not None:
        view_height = int(tail_lines)

    safe_mkdir(log_file.parent)

    VIEW_H = max(5, int(view_height))
    BUF_MAX = max(VIEW_H * 10, int(buffer_max))

    buf_lines: deque[str] = deque(maxlen=BUF_MAX)
    pending_partial = ""  # última línea incompleta (solo UI)

    # cache para slicing sin list() cada vez
    _cache_list: List[str] = []
    _cache_dirty = True

    def _mark_dirty() -> None:
        nonlocal _cache_dirty
        _cache_dirty = True

    def _ensure_cache() -> List[str]:
        nonlocal _cache_list, _cache_dirty
        if _cache_dirty:
            _cache_list = list(buf_lines)
            _cache_dirty = False
        return _cache_list

    start = time.time()
    dots_frames = ["", ".", "..", "..."]
    frame_idx = 0

    OUTPUT_REFRESH_MIN = 0.02
    ANIM_REFRESH = 0.25
    ANIM_ONLY_IF_IDLE = 0.40

    # cola "drop" para UI
    dq = _DropQueue(max_items=250)
    data_event = threading.Event()

    # scroll_offset: líneas desde el final (0 = bottom/follow)
    scroll_offset = 0

    def total_lines() -> int:
        return len(buf_lines) + (1 if pending_partial else 0)

    def max_scroll() -> int:
        return max(0, total_lines() - VIEW_H)

    def get_view_text() -> str:
        lines = _ensure_cache()
        n_base = len(lines)
        has_partial = 1 if pending_partial else 0
        n_total = n_base + has_partial

        if n_total == 0:
            view: List[str] = []
        else:
            end = n_total - scroll_offset
            end = _clamp(end, 0, n_total)
            start_i = max(0, end - VIEW_H)

            view = []
            # parte en base_lines
            if start_i < n_base:
                end_base = min(end, n_base)
                if end_base > start_i:
                    view.extend(lines[start_i:end_base])
            # parte en partial (si aplica)
            if has_partial and end > n_base:
                if start_i <= n_base:
                    view.append(pending_partial)

        if len(view) < VIEW_H:
            view = ([""] * (VIEW_H - len(view))) + view
        return "\n".join(view)

    def poll_scroll_keys(new_lines_count: int) -> bool:
        nonlocal scroll_offset
        if new_lines_count > 0 and scroll_offset > 0:
            scroll_offset += new_lines_count
        scroll_offset = _clamp(scroll_offset, 0, max_scroll())

        if not HAS_MSVCRT:
            return False

        changed = False
        while msvcrt.kbhit():  # type: ignore[name-defined]
            ch = msvcrt.getwch()  # type: ignore[name-defined]

            if ch == "\x03":
                raise KeyboardInterrupt

            if ch in ("\x00", "\xe0"):
                k = msvcrt.getwch()  # type: ignore[name-defined]
                if k == "H":        # Up
                    scroll_offset += 1; changed = True
                elif k == "P":      # Down
                    scroll_offset -= 1; changed = True
                elif k == "I":      # PgUp
                    scroll_offset += VIEW_H; changed = True
                elif k == "Q":      # PgDn
                    scroll_offset -= VIEW_H; changed = True
                elif k == "G":      # Home
                    scroll_offset = max_scroll(); changed = True
                elif k == "O":      # End
                    scroll_offset = 0; changed = True
            else:
                if ch in ("k", "K"):
                    scroll_offset += 1; changed = True
                elif ch in ("j", "J"):
                    scroll_offset -= 1; changed = True
                elif ch in ("g", "G"):
                    scroll_offset = max_scroll(); changed = True
                elif ch == "0":
                    scroll_offset = 0; changed = True

        if changed:
            scroll_offset = _clamp(scroll_offset, 0, max_scroll())
        return changed

    proc: Optional[subprocess.Popen[bytes]] = None

    out_text = Text("", style="bright_green", overflow="crop", no_wrap=True)
    status_text = Text("Ejecutando", style="bold cyan")
    panel = Panel(out_text, title=title, subtitle=f"Log completo: {log_file}", box=box.ROUNDED)
    renderable = Group(panel, status_text)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,  # bytes, sin buffering de python
        )
        assert proc.stdout is not None

        def reader() -> None:
            with log_file.open("wb") as lf:
                while True:
                    chunk = proc.stdout.read(65536)  # type: ignore[arg-type]
                    if not chunk:
                        break
                    lf.write(chunk)
                    dq.put(chunk)      # UI (drop si hace falta)
                    data_event.set()
                lf.flush()

            dq.put(None)  # EOF
            data_event.set()

        threading.Thread(target=reader, daemon=True).start()

        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        last_output = time.monotonic()
        last_ui = 0.0
        last_anim = 0.0
        last_size = console.size

        with Live(renderable, console=console, auto_refresh=False) as live:
            out_text.plain = get_view_text()
            status_text.plain = "Ejecutando"
            live.refresh()
            last_ui = time.monotonic()

            eof = False

            while True:
                got_any = False
                new_lines = 0

                # drena todo lo disponible (rápido)
                while True:
                    try:
                        b = dq.get_nowait()
                    except queue.Empty:
                        break

                    got_any = True
                    last_output = time.monotonic()

                    if b is None:
                        eof = True
                        break

                    txt = decoder.decode(b)
                    if not txt:
                        continue
                    txt = txt.replace("\r\n", "\n").replace("\r", "\n")

                    # parse incremental por \n
                    chunk_pending = pending_partial + txt
                    parts = chunk_pending.split("\n")
                    pending_partial = parts.pop()  # última incompleta
                    if parts:
                        for ln in parts:
                            buf_lines.append(ln)
                            new_lines += 1
                        _mark_dirty()

                # input scroll
                user_scrolled = poll_scroll_keys(new_lines)

                now = time.monotonic()

                # resize
                sz = console.size
                resized = sz != last_size
                if resized:
                    last_size = sz

                # refresh UI (cuando toca)
                if (got_any and (now - last_ui) >= OUTPUT_REFRESH_MIN) or user_scrolled or resized:
                    out_text.plain = get_view_text()
                    if scroll_offset == 0:
                        status_text.plain = "Ejecutando"
                        status_text.style = "bold cyan"
                    else:
                        status_text.plain = f"Scroll: {scroll_offset} (End para seguir)"
                        status_text.style = "bold yellow"
                    live.refresh()
                    last_ui = now

                # animación idle
                if scroll_offset == 0:
                    if (now - last_output) >= ANIM_ONLY_IF_IDLE and (now - last_anim) >= ANIM_REFRESH:
                        frame_idx = (frame_idx + 1) % len(dots_frames)
                        status_text.plain = f"Ejecutando{dots_frames[frame_idx]}"
                        status_text.style = "bold cyan"
                        live.refresh()
                        last_anim = now

                if eof:
                    rc = proc.wait()
                    elapsed = human_dt(time.time() - start)

                    out_text.plain = get_view_text()
                    if rc == 0:
                        status_text.plain = f"Terminado ✅  (t={elapsed})"
                        status_text.style = "bold green"
                    else:
                        status_text.plain = f"Falló ❌ (rc={rc})  (t={elapsed})"
                        status_text.style = "bold red"
                    live.refresh()
                    return int(rc)

                # espera eficiente: si no hay data, duerme poco y despierta por event
                if not got_any:
                    data_event.wait(timeout=0.05)
                    data_event.clear()
                else:
                    # cede CPU
                    time.sleep(0.002)

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrumpido (Ctrl+C).[/]")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        return 130


def run_passthrough(cmd: List[str], cwd: Optional[Path]) -> int:
    try:
        completed = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
        return int(completed.returncode)
    except KeyboardInterrupt:
        return 130


# =========================
# Command builders
# =========================
def compose_base(compose_file: Path, project_dir: Optional[Path]) -> List[str]:
    base = ["docker", "compose", "-f", str(compose_file)]
    if project_dir:
        base += ["--project-directory", str(project_dir)]
    return base


def build_action_cmd(
    a: Action,
    compose_file: Path,
    project_dir: Optional[Path],
    services: List[str],
) -> Tuple[List[str], str]:
    if a.type == "shell":
        if not a.cmd:
            raise ValueError("shell: falta cmd")
        cmd_str = a.cmd.strip()
        if cmd_str.lower().endswith((".bat", ".cmd")):
            return ["cmd.exe", "/c", cmd_str], f"cmd /c {cmd_str}"
        if cmd_str.lower().endswith(".ps1"):
            return ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", cmd_str], f"powershell -File {cmd_str}"
        # .sh: preferir bash, fallback a wsl bash
        if cmd_str.lower().endswith(".sh"):
            bash = shutil.which("bash") or shutil.which("bash.exe")
            if bash:
                return [bash, cmd_str], f"bash {cmd_str}"
            wsl = shutil.which("wsl")
            if wsl:
                wd = project_dir or Path.cwd()
                wd_wsl = win_to_wsl_path(wd)
                script_name = Path(cmd_str).name
                return ["wsl", "bash", "-lc", f"cd '{wd_wsl}' && bash '{script_name}'"], f"wsl bash {script_name}"

            return ["cmd.exe", "/c", cmd_str], cmd_str
        return ["cmd.exe", "/c", cmd_str], cmd_str

    if a.type == "compose":
        if not a.subcmd:
            raise ValueError("compose: falta subcmd")
        parts = shlex.split(a.subcmd, posix=False)
        cmd = compose_base(compose_file, project_dir) + parts
        return cmd, "docker compose " + a.subcmd
    
    if a.type == "compose_pull_select":
        picked = choose_services_multi(services)
        if not picked:
            return ["cmd.exe", "/c", "echo No se selecciono ningun servicio."], "pull (sin seleccion)"

        cmd = compose_base(compose_file, project_dir) + ["pull"] + picked
        return cmd, "docker compose pull " + " ".join(picked)


    if a.type == "compose_logs":
        service = a.service or ""
        if not service or service == "prompt":
            service = choose_service_from_list(services)
        cmd = compose_base(compose_file, project_dir) + ["logs", "--tail", str(int(a.tail))]
        if a.follow:
            cmd.append("-f")
        if service:
            cmd.append(service)
        return cmd, f"docker compose logs {service or '<service>'}"

    if a.type == "docker_exec":
        if not a.container:
            raise ValueError("docker_exec: falta container")
        if not a.exec_cmd:
            raise ValueError("docker_exec: falta exec_cmd")
        cmd = ["docker", "exec"]
        if a.stdin:
            cmd.append("-i")
        if a.tty:
            cmd.append("-t")
        cmd += [a.container]
        cmd += shlex.split(a.exec_cmd, posix=True)
        return cmd, f"docker exec {a.container} …"

    if a.type == "compose_exec":
        service = a.service or ""
        if not service or service == "prompt":
            service = choose_service_from_list(services)
        if not a.exec_cmd:
            raise ValueError("compose_exec: falta exec_cmd")
        cmd = compose_base(compose_file, project_dir) + ["exec"]
        if a.mode == "live" and not a.tty:
            cmd.append("-T")
        if service:
            cmd.append(service)
        cmd += shlex.split(a.exec_cmd, posix=True)
        return cmd, f"docker compose exec {service or '<service>'} …"

    raise ValueError(f"Tipo de acción no soportado: {a.type}")


def build_commands(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], List[CommandDef], Dict[int, List[int]]]:
    app = cfg.get("app") or {}

    combos_cfg = cfg.get("combos") or []
    combos: Dict[int, List[int]] = {}
    for c in combos_cfg:
        combos[int(c["id"])] = [int(x) for x in c.get("run", [])]

    cmds_cfg = cfg.get("commands") or []
    commands: List[CommandDef] = []
    for c in cmds_cfg:
        cid = int(c["id"])
        title = str(c.get("title", f"Cmd {cid}"))
        desc = str(c.get("desc", ""))

        actions_raw = c.get("actions") or []
        actions: List[Action] = []
        for ar in actions_raw:
            actions.append(
                Action(
                    type=str(ar.get("type", "shell")),
                    cmd=ar.get("cmd"),
                    subcmd=ar.get("subcmd"),
                    service=ar.get("service"),
                    container=ar.get("container"),
                    exec_cmd=ar.get("exec_cmd"),
                    tty=bool(ar.get("tty", False)),
                    stdin=bool(ar.get("stdin", False)),
                    mode=str(ar.get("mode", "live")),
                    tail_lines=int(ar.get("tail_lines", app.get("default_tail_lines", 60))),
                    follow=bool(ar.get("follow", False)),
                    tail=int(ar.get("tail", 200)),
                )
            )

        commands.append(CommandDef(id=cid, title=title, desc=desc, actions=actions))

    commands.sort(key=lambda x: x.id)
    return app, commands, combos


# =========================
# Main
# =========================
def main() -> int:
    parser = argparse.ArgumentParser(description="ZenoVerso CLI")
    parser.add_argument("--config", help="Ruta a zenoverso.yml (override)", default=None)
    parser.add_argument("--run", help="Ejecuta IDs sin UI (ej: 1,3,7)", default=None)
    args = parser.parse_args()

    which_or_die("docker")

    cfg_path, cfg = find_config(args.config)
    app_cfg, commands, combos = build_commands(cfg)

    app_name = str(app_cfg.get("name") or "ZenoVerso CLI")
    author = str(app_cfg.get("author") or "Quino")
    clear_screen = bool(app_cfg.get("clear_screen", True))
    use_alt_screen = bool(app_cfg.get("use_alt_screen", True))

    compose_file = Path(str(app_cfg.get("compose_file") or "docker-compose.yml"))
    project_dir = Path(str(app_cfg.get("project_dir"))) if app_cfg.get("project_dir") else None

    log_dir = Path(str(app_cfg.get("log_dir") or (Path.cwd() / "logs")))
    safe_mkdir(log_dir)

    runtime_home = Path(str(app_cfg.get("runtime_home") or log_dir.parent))
    history_file = Path(str(app_cfg.get("history_file") or (runtime_home / "history.ndjson")))
    history_keep_max = int(app_cfg.get("history_keep_max", 500))
    history_show_last = int(app_cfg.get("history_show_last", 8))

    output_buffer_max = int(app_cfg.get("output_buffer_max", 5000))

    history_cache = load_history(history_file, keep_max=history_keep_max)

    services = load_compose_services(compose_file)
    by_id = {c.id: c for c in commands}

    def record_history_entry(entry: Dict[str, Any]) -> None:
        nonlocal history_cache
        append_history(history_file, entry)
        history_cache.append(entry)
        if len(history_cache) > max(50, history_keep_max):
            history_cache = history_cache[-history_keep_max:]

    def execute_plan(plan: List[int]) -> int:
        expanded: List[int] = []
        for cid in plan:
            expanded.extend(combos.get(cid, [cid]))

        invalid = [x for x in expanded if x not in by_id]
        if invalid:
            console.print(f"[bold red]IDs inválidos:[/] {invalid}")
            return 2

        overall_rc = 0

        for cid in expanded:
            cmddef = by_id[cid]

            cmd_start_wall = now_iso()
            cmd_start_mono = time.time()

            ok = True
            cmd_logs: List[Dict[str, Any]] = []

            for idx, action in enumerate(cmddef.actions, start=1):
                if clear_screen:
                    console.clear()

                # pantalla de ejecución
                console.print(render_header(app_name, author, cfg_path))
                console.print(build_menu_table(commands))
                console.print(build_shortcuts_panel())
                console.print(
                    Panel(
                        f"Plan de ejecución: [bold]{expanded}[/]\n\n"
                        f"Ejecutando: [bold]{cid}) {cmddef.title}[/]\n"
                        f"Acción: {idx}/{len(cmddef.actions)}",
                        box=box.ROUNDED,
                    )
                )

                cmd, pretty = build_action_cmd(action, compose_file, project_dir, services)
                log_file = log_dir / f"{ts_compact()}__cmd{cid}__a{idx}.log"

                if action.mode == "passthrough":
                    console.print(Panel(f"Passthrough: [dim]{pretty}[/]\n(Sal con 'exit' para volver)", box=box.ROUNDED))
                    rc = run_passthrough(cmd, cwd=project_dir)
                else:
                    rc = run_live_command(
                        cmd=cmd,
                        title=pretty,
                        log_file=log_file,
                        view_height=action.tail_lines,
                        cwd=project_dir,
                        buffer_max=output_buffer_max,
                    )

                if rc == 130 and action.follow:
                    rc = 0

                cmd_logs.append(
                    {"action_index": idx, "pretty": pretty, "log_file": str(log_file), "rc": rc}
                )

                if rc != 0:
                    ok = False
                    overall_rc = overall_rc or rc

            elapsed = human_dt(time.time() - cmd_start_mono)

            entry = {
                "start": cmd_start_wall,
                "end": now_iso(),
                "duration_human": elapsed,
                "duration_seconds": int(time.time() - cmd_start_mono),
                "plan": expanded,
                "cmd_id": cid,
                "cmd_title": cmddef.title,
                "ok": ok,
                "logs": cmd_logs,
            }
            record_history_entry(entry)

            if clear_screen:
                console.clear()

        return overall_rc

    def run_app() -> int:
        nonlocal cfg_path, cfg, app_cfg, commands, combos, by_id, clear_screen, use_alt_screen
        nonlocal compose_file, project_dir, log_dir, runtime_home, history_file, history_keep_max, history_show_last
        nonlocal history_cache, services, output_buffer_max

        if args.run:
            plan = parse_selection(args.run)
            return execute_plan(plan)

        while True:
            try:
                raw = ask_line_home(
                    app_name=app_name,
                    author=author,
                    cfg_path=cfg_path,
                    commands=commands,
                    history_cache=history_cache,
                    history_show_last=history_show_last,
                    history_file=history_file,
                    clear_screen=clear_screen,
                )
            except KeyboardInterrupt:
                return 130

            raw_l = raw.strip().lower()

            if raw_l in {"q", "quit", "exit"}:
                return 0

            if raw_l in {"r", "reload"}:
                cfg_path, cfg = find_config(args.config)
                app_cfg, commands, combos = build_commands(cfg)
                by_id = {c.id: c for c in commands}

                clear_screen = bool(app_cfg.get("clear_screen", True))
                use_alt_screen = bool(app_cfg.get("use_alt_screen", True))

                compose_file = Path(str(app_cfg.get("compose_file") or "docker-compose.yml"))
                project_dir = Path(str(app_cfg.get("project_dir"))) if app_cfg.get("project_dir") else None

                log_dir = Path(str(app_cfg.get("log_dir") or (Path.cwd() / "logs")))
                safe_mkdir(log_dir)

                runtime_home = Path(str(app_cfg.get("runtime_home") or log_dir.parent))
                history_file = Path(str(app_cfg.get("history_file") or (runtime_home / "history.ndjson")))
                history_keep_max = int(app_cfg.get("history_keep_max", 500))
                history_show_last = int(app_cfg.get("history_show_last", 8))
                output_buffer_max = int(app_cfg.get("output_buffer_max", 5000))

                history_cache = load_history(history_file, keep_max=history_keep_max)
                services = load_compose_services(compose_file)
                continue

            try:
                plan = parse_selection(raw)
            except ValueError as e:
                if clear_screen:
                    console.clear()
                console.print(render_header(app_name, author, cfg_path))
                console.print(Panel(f"[bold red]{e}[/]", box=box.ROUNDED))
                time.sleep(0.8)
                continue

            if not plan:
                continue

            execute_plan(plan)

    if use_alt_screen:
        with console.screen(style="black"):
            return run_app()
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())

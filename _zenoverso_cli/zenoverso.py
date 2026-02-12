#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from rich import box

import threading
import queue
from rich.console import Group

console = Console()


# -----------------------
# Helpers básicos
# -----------------------
def ts_compact() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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
    # Si es PyInstaller --onefile: sys.executable apunta al .exe real
    # Si es python normal: __file__
    base = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    return base.parent


def read_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def find_config(cli_path: Optional[str]) -> Tuple[Path, Dict[str, Any]]:
    # Prioridad:
    # 1) --config
    # 2) env ZENOVERSO_CONFIG
    # 3) junto al exe/script (app_base_dir)
    # 4) fallback a repo (por si ejecutas desde fuente sin instalar)
    candidates: List[Path] = []

    if cli_path:
        candidates.append(Path(cli_path))

    envp = os.environ.get("ZENOVERSO_CONFIG")
    if envp:
        candidates.append(Path(envp))

    candidates.append(app_base_dir() / "zenoverso.yml")

    # fallback típico dentro del repo (por comodidad de dev)
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


def load_compose_services(compose_file: Path) -> List[str]:
    if not compose_file.exists():
        return []
    try:
        data = yaml.safe_load(compose_file.read_text(encoding="utf-8", errors="ignore")) or {}
        services = (data.get("services") or {}).keys()
        return sorted(list(services))
    except Exception:
        return []


# -----------------------
# Modelos
# -----------------------
@dataclass
class Action:
    type: str                      # shell | compose | compose_logs | docker_exec | compose_exec
    cmd: Optional[str] = None      # shell
    subcmd: Optional[str] = None   # compose
    service: Optional[str] = None  # compose_logs / compose_exec
    container: Optional[str] = None  # docker_exec
    exec_cmd: Optional[str] = None    # docker_exec / compose_exec
    tty: bool = False
    stdin: bool = False
    mode: str = "live"             # live | passthrough
    tail_lines: int = 60
    follow: bool = False
    tail: int = 200                # para logs


@dataclass
class CommandDef:
    id: int
    title: str
    desc: str
    actions: List[Action]


# -----------------------
# Render UI
# -----------------------
def render_header(app_name: str, author: str, cfg_path: Path) -> None:
    title = Text(app_name, style="bold cyan")
    subtitle = Text(f"Creado por {author}  ·  Config: {cfg_path}", style="dim")
    console.print(Panel.fit(Text.assemble(title, "\n", subtitle), box=box.ROUNDED))


def render_menu(commands: List[CommandDef]) -> None:
    table = Table(title="Comandos disponibles", box=box.SIMPLE_HEAVY, show_lines=True)
    table.add_column("ID", style="bold yellow", width=4, justify="right")
    table.add_column("Comando", style="bold")
    table.add_column("Descripción")

    for c in commands:
        table.add_row(str(c.id), c.title, c.desc)

    console.print(table)
    console.print(
        Panel(
            "[bold]Uso:[/]\n"
            "• IDs en orden: [cyan]1,3,7[/] o [cyan]1 3 7[/]\n"
            "• Rangos: [cyan]2-5[/]\n"
            "• [cyan]r[/] recargar config · [cyan]q[/] salir",
            title="Atajos",
            box=box.ROUNDED,
        )
    )


# -----------------------
# Ejecutores
# -----------------------
def run_live_command(
    cmd: List[str],
    title: str,
    log_file: Path,
    tail_lines: int,
    cwd: Optional[Path],
) -> int:
    safe_mkdir(log_file.parent)

    # Altura fija del panel: por defecto usamos tail_lines, y tú lo pondrás a 25
    FIXED_HEIGHT = max(5, int(tail_lines))

    lines = deque(maxlen=FIXED_HEIGHT)
    start = time.time()

    # Animación
    dots_frames = ["", ".", "..", "..."]
    frame_idx = 0

    # Refresco suave (sin flicker)
    TICK = 0.12  # ~8 FPS, suficiente para animación sin parpadeo
    last_tick = 0.0

    # Hilo lector de stdout -> cola
    q: "queue.Queue[Optional[str]]" = queue.Queue()

    def pad_to_height(text_lines: List[str]) -> str:
        # Rellena con líneas vacías hasta FIXED_HEIGHT
        padded = text_lines[-FIXED_HEIGHT:]
        if len(padded) < FIXED_HEIGHT:
            padded = ([""] * (FIXED_HEIGHT - len(padded))) + padded
        return "\n".join(padded)

    def make_render(status_line: str, panel_status: str) -> Group:
        body = pad_to_height(list(lines))
        panel = Panel(
            Text(body, overflow="crop", no_wrap=True),
            title=f"{title}  [dim]({panel_status})[/]",
            subtitle=f"Log completo: {log_file}",
            box=box.ROUNDED,
        )
        # Línea fuera del panel (abajo)
        status = Text(status_line, style="bold cyan")
        return Group(panel, status)

    proc: Optional[subprocess.Popen[str]] = None

    try:
        with log_file.open("w", encoding="utf-8", errors="ignore") as lf:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
            assert proc.stdout is not None

            # Lector en hilo (para poder animar aunque no haya output nuevo)
            def reader():
                try:
                    for line in proc.stdout:  # type: ignore[union-attr]
                        q.put(line.rstrip("\r\n"))
                finally:
                    q.put(None)  # sentinel EOF

            t = threading.Thread(target=reader, daemon=True)
            t.start()

            # Live sin auto_refresh -> cero flicker
            with Live(
                make_render("Ejecutando", "arrancando…"),
                console=console,
                auto_refresh=False,
            ) as live:

                eof = False
                last_tick = time.monotonic()

                # Bucle principal: procesa líneas de cola + anima por ticks
                while True:
                    # 1) Consume todas las líneas disponibles sin bloquear
                    got_any = False
                    while True:
                        try:
                            item = q.get_nowait()
                        except queue.Empty:
                            break

                        if item is None:
                            eof = True
                            break

                        got_any = True
                        lines.append(item)
                        lf.write(item + "\n")

                    if got_any:
                        lf.flush()

                    # 2) Tick de animación / refresco throttled
                    now = time.monotonic()
                    if (now - last_tick) >= TICK:
                        frame_idx = (frame_idx + 1) % len(dots_frames)
                        status_line = f"Ejecutando{dots_frames[frame_idx]}"
                        live.update(make_render(status_line, "en ejecución…"))
                        live.refresh()
                        last_tick = now

                    # 3) Condición de salida: EOF y proceso terminado
                    if eof:
                        rc = proc.wait()
                        elapsed = human_dt(time.time() - start)
                        # Render final: deja últimas 25 líneas y estado terminado
                        live.update(make_render("Terminado ✅", f"terminado · rc={rc} · t={elapsed}"))
                        live.refresh()
                        return rc

                    # Pequeño sleep para no quemar CPU
                    time.sleep(0.02)

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


def compose_base(compose_file: Path, project_dir: Optional[Path]) -> List[str]:
    base = ["docker", "compose", "-f", str(compose_file)]
    if project_dir:
        base += ["--project-directory", str(project_dir)]
    return base


def choose_from_list(title: str, items: List[str]) -> str:
    if not items:
        return Prompt.ask(f"{title} (no pude leer servicios; escribe a mano)", default="")
    console.print(Panel("\n".join(f"• {x}" for x in items), title=title, box=box.ROUNDED))
    while True:
        val = Prompt.ask(f"{title} (exacto)", default=items[0])
        if val in items:
            return val
        console.print("[red]No coincide. Copia/pega uno de la lista.[/]")


def build_action_cmd(
    a: Action,
    compose_file: Path,
    project_dir: Optional[Path],
    services: List[str],
) -> Tuple[List[str], str]:
    if a.type == "shell":
        # Ejecutar .bat/.cmd vía cmd /c; .ps1 vía powershell
        if not a.cmd:
            raise ValueError("shell: falta cmd")
        cmd_str = a.cmd.strip()

        if cmd_str.lower().endswith((".bat", ".cmd")):
            return ["cmd.exe", "/c", cmd_str], f"cmd /c {cmd_str}"
        if cmd_str.lower().endswith(".ps1"):
            return ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", cmd_str], f"powershell -File {cmd_str}"
        return ["cmd.exe", "/c", cmd_str], cmd_str

    if a.type == "compose":
        if not a.subcmd:
            raise ValueError("compose: falta subcmd")
        parts = shlex.split(a.subcmd, posix=False)
        cmd = compose_base(compose_file, project_dir) + parts
        return cmd, "docker compose " + a.subcmd

    if a.type == "compose_logs":
        service = a.service or "prompt"
        if service == "prompt":
            service = choose_from_list("Servicio para logs", services)
        cmd = compose_base(compose_file, project_dir) + ["logs", "--tail", str(int(a.tail))]
        if a.follow:
            cmd.append("-f")
        cmd.append(service)
        return cmd, f"docker compose logs {service}"

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
        service = a.service or "prompt"
        if service == "prompt":
            service = choose_from_list("Servicio (compose exec)", services)
        if not a.exec_cmd:
            raise ValueError("compose_exec: falta exec_cmd")
        cmd = compose_base(compose_file, project_dir) + ["exec"]
        # En modo live, desactivamos TTY para capturar bien la salida
        if a.mode == "live" and not a.tty:
            cmd.append("-T")
        cmd.append(service)
        cmd += shlex.split(a.exec_cmd, posix=True)
        return cmd, f"docker compose exec {service} …"

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

    compose_file = Path(str(app_cfg.get("compose_file") or "docker-compose.yml"))
    project_dir = Path(str(app_cfg.get("project_dir"))) if app_cfg.get("project_dir") else None

    log_dir = Path(str(app_cfg.get("log_dir") or (Path.cwd() / "logs")))
    safe_mkdir(log_dir)

    services = load_compose_services(compose_file)

    by_id = {c.id: c for c in commands}

    def execute_plan(plan: List[int]) -> int:
        expanded: List[int] = []
        for cid in plan:
            expanded.extend(combos.get(cid, [cid]))

        invalid = [x for x in expanded if x not in by_id]
        if invalid:
            console.print(f"[bold red]IDs inválidos:[/] {invalid}")
            return 2

        if clear_screen:
            console.clear()
        render_header(app_name, author, cfg_path)
        console.print(Panel(f"Plan de ejecución: [bold]{expanded}[/]", box=box.ROUNDED))

        results = []
        overall_rc = 0

        for cid in expanded:
            cmddef = by_id[cid]
            console.print(Panel(f"[bold]{cid}) {cmddef.title}[/]\n{cmddef.desc}", box=box.ROUNDED))
            start = time.time()
            ok = True

            for idx, action in enumerate(cmddef.actions, start=1):
                cmd, pretty = build_action_cmd(action, compose_file, project_dir, services)
                log_file = log_dir / f"{ts_compact()}__cmd{cid}__a{idx}.log"

                if action.mode == "passthrough":
                    console.print(f"[dim]→ {pretty}  (passthrough)[/]")
                    rc = run_passthrough(cmd, cwd=project_dir)
                else:
                    rc = run_live_command(
                        cmd=cmd,
                        title=pretty,
                        log_file=log_file,
                        tail_lines=action.tail_lines,
                        cwd=project_dir,
                    )

                # Si estás en logs -f y sales con Ctrl+C, lo tratamos como salida normal.
                if rc == 130 and action.follow:
                    rc = 0

                if rc != 0:
                    ok = False
                    overall_rc = overall_rc or rc
                    console.print(f"[bold red]FALLÓ[/] acción {idx} (rc={rc}). Log: {log_file}")

            elapsed = human_dt(time.time() - start)
            results.append((cid, cmddef.title, ok, elapsed))
            console.print(f"[bold]{cid})[/] {'[green]OK[/]' if ok else '[red]FAIL[/]'}  [dim](t={elapsed})[/]\n")

        table = Table(title="Resumen", box=box.SIMPLE_HEAVY, show_lines=True)
        table.add_column("ID", style="bold yellow", width=4)
        table.add_column("Comando")
        table.add_column("Resultado")
        table.add_column("Tiempo", justify="right")
        for cid, title, ok, elapsed in results:
            table.add_row(str(cid), title, "[green]OK[/]" if ok else "[red]FAIL[/]", elapsed)
        console.print(table)

        return overall_rc

    # Modo no interactivo: zenoverso --run 1,3,7
    if args.run:
        plan = parse_selection(args.run)
        return execute_plan(plan)

    # UI loop
    while True:
        if clear_screen:
            console.clear()
        render_header(app_name, author, cfg_path)
        render_menu(commands)

        raw = Prompt.ask("[bold cyan]Selecciona[/] (IDs / r / q)", default="")
        raw_l = raw.strip().lower()

        if raw_l in {"q", "quit", "exit"}:
            return 0

        if raw_l in {"r", "reload"}:
            cfg_path, cfg = find_config(args.config)
            app_cfg, commands, combos = build_commands(cfg)
            by_id = {c.id: c for c in commands}
            clear_screen = bool(app_cfg.get("clear_screen", True))
            compose_file = Path(str(app_cfg.get("compose_file") or "docker-compose.yml"))
            project_dir = Path(str(app_cfg.get("project_dir"))) if app_cfg.get("project_dir") else None
            log_dir = Path(str(app_cfg.get("log_dir") or (Path.cwd() / "logs")))
            safe_mkdir(log_dir)
            services = load_compose_services(compose_file)
            continue

        try:
            plan = parse_selection(raw)
        except ValueError as e:
            console.print(f"[bold red]{e}[/]")
            Prompt.ask("ENTER para continuar", default="")
            continue

        if not plan:
            continue

        rc = execute_plan(plan)
        # No pedimos ENTER: volvemos directo a pedir más opciones
        # Prompt.ask("ENTER para volver al menú", default="")


if __name__ == "__main__":
    raise SystemExit(main())

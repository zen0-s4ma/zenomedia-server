#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

def run(cmd: List[str]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout.strip()

def compose_cmd(compose_file: str | None, project: str | None, args: List[str]) -> List[str]:
    cmd = ["docker", "compose"]
    if compose_file:
        cmd += ["-f", compose_file]
    if project:
        cmd += ["-p", project]
    cmd += args
    return cmd

def get_container_ids(compose_file: str | None, project: str | None) -> List[str]:
    out = run(compose_cmd(compose_file, project, ["ps", "-q"]))
    ids = [x.strip() for x in out.splitlines() if x.strip()]
    return ids

def inspect_containers(ids: List[str]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    out = run(["docker", "inspect"] + ids)
    return json.loads(out)

def classify(containers: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, str]]]:
    ok: List[Dict[str, str]] = []
    fail: List[Dict[str, str]] = []
    pending: List[Dict[str, str]] = []

    for c in containers:
        name = (c.get("Name") or "").lstrip("/") or "unknown"
        labels = (c.get("Config") or {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service", name)
        state = c.get("State") or {}
        status = state.get("Status", "unknown")
        exit_code = str(state.get("ExitCode", ""))
        health_obj = state.get("Health")
        health = health_obj.get("Status") if isinstance(health_obj, dict) else None

        last_output = ""
        if isinstance(health_obj, dict):
            log = health_obj.get("Log") or []
            if isinstance(log, list) and log:
                last_output = (log[-1].get("Output") or "").strip()
                # recorta para que no sea infinito
                if len(last_output) > 240:
                    last_output = last_output[:240] + "…"

        row = {
            "service": service,
            "container": name,
            "status": status,
            "health": health or "no-healthcheck",
            "exit": exit_code,
            "detail": last_output
        }

        # Estado runtime manda
        if status != "running":
            row["detail"] = row["detail"] or f"State.Status={status}, ExitCode={exit_code}"
            fail.append(row)
            continue

        # Si está running, decide por healthcheck si existe
        if health is None:
            ok.append(row)  # running sin healthcheck
        elif health == "healthy":
            ok.append(row)
        elif health == "unhealthy":
            row["detail"] = row["detail"] or "Healthcheck failing"
            fail.append(row)
        else:
            # starting / unknown
            pending.append(row)

    # orden estable por nombre de servicio
    ok.sort(key=lambda x: x["service"])
    fail.sort(key=lambda x: x["service"])
    pending.sort(key=lambda x: x["service"])
    return ok, fail, pending

def print_report(ok: List[Dict[str, str]], fail: List[Dict[str, str]]) -> None:
    print("\n=== OK ===")
    if not ok:
        print("  (ninguno)")
    for r in ok:
        h = r["health"]
        if h == "no-healthcheck":
            print(f"  ✓ {r['service']:<20}  {r['container']}  running  (sin healthcheck)")
        else:
            print(f"  ✓ {r['service']:<20}  {r['container']}  running  ({h})")

    print("\n=== FAIL ===")
    if not fail:
        print("  (ninguno)")
    for r in fail:
        detail = f"  :: {r['detail']}" if r["detail"] else ""
        print(f"  ✗ {r['service']:<20}  {r['container']}  {r['status']}  ({r['health']}){detail}")

def main() -> int:
    ap = argparse.ArgumentParser(description="Resume healthchecks (OK vs FAIL) de un proyecto docker compose.")
    ap.add_argument("-f", "--compose-file", default=None, help="Ruta a docker-compose.yml")
    ap.add_argument("-p", "--project", default=None, help="Nombre del proyecto (opcional)")
    ap.add_argument("-t", "--timeout", type=int, default=300, help="Timeout total de espera (segundos)")
    ap.add_argument("-i", "--interval", type=int, default=5, help="Intervalo de sondeo (segundos)")
    ap.add_argument("--no-wait", action="store_true", help="No esperar a que termine 'starting'; solo snapshot")
    args = ap.parse_args()

    start = time.time()
    timed_out = False

    ids = get_container_ids(args.compose_file, args.project)
    if not ids:
        print("No hay contenedores para ese compose/proyecto (¿stack levantado?).", file=sys.stderr)
        return 1

    while True:
        containers = inspect_containers(ids)
        ok, fail, pending = classify(containers)

        if args.no_wait:
            print_report(ok, fail)
            return 2 if fail else 0

        if not pending:
            # estable: ya no hay "starting/unknown"
            print_report(ok, fail)
            return 2 if fail else 0

        if time.time() - start >= args.timeout:
            timed_out = True
            # si hay pending y se agotó el tiempo, los tratamos como FAIL (timeout)
            for r in pending:
                r["status"] = "running"
                r["health"] = r["health"]
                r["detail"] = (r["detail"] or "") + " (TIMEOUT esperando a estado estable)"
                fail.append(r)
            pending.clear()
            print_report(ok, fail)
            return 3

        time.sleep(args.interval)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

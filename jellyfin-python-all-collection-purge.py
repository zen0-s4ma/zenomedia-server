#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Purge ALL Jellyfin collections (BoxSet items) in a clean, safe, logged way.

- Lists BoxSet items via GET /Items?includeItemTypes=BoxSet&recursive=true (paged)
- Deletes each via DELETE /Items/{itemId}
- Writes backup JSON + log file next to the script
- Optionally triggers Scheduled Task "Clean up collections and playlists"

Auth:
- Uses modern Authorization header scheme:
  Authorization: MediaBrowser Token="API_KEY", Client="...", Device="...", DeviceId="...", Version="..."
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# =========================
# CONFIG (todo junto arriba)
# =========================

JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://192.168.1.113:8096").rstrip("/")
API_KEY = os.getenv("JELLYFIN_API_KEY", "PEGA_AQUI_TU_API_KEY")

# Identidad del “cliente” (solo para que Jellyfin lo vea en Dashboard)
CLIENT_NAME = os.getenv("JELLYFIN_CLIENT_NAME", "Zenoverso-Collections-Purger")
CLIENT_VERSION = os.getenv("JELLYFIN_CLIENT_VERSION", "1.0.0")
DEVICE_NAME = os.getenv("JELLYFIN_DEVICE_NAME", "Windows-Host")
# DeviceId debería ser estable; si no lo pones, generamos uno determinista por máquina/usuario
DEVICE_ID = os.getenv("JELLYFIN_DEVICE_ID", f"zenoverso-{uuid.getnode()}")

# Red / HTTP
VERIFY_TLS = os.getenv("JELLYFIN_VERIFY_TLS", "false").lower() in ("1", "true", "yes", "y")
TIMEOUT_SECONDS = int(os.getenv("JELLYFIN_TIMEOUT", "30"))
RETRIES = int(os.getenv("JELLYFIN_RETRIES", "5"))
RETRY_BASE_SLEEP = float(os.getenv("JELLYFIN_RETRY_BASE_SLEEP", "0.8"))

# Paginación
PAGE_SIZE = int(os.getenv("JELLYFIN_PAGE_SIZE", "500"))

# Post-limpieza
TRIGGER_CLEANUP_TASK = os.getenv("JELLYFIN_TRIGGER_CLEANUP_TASK", "true").lower() in ("1", "true", "yes", "y")
WAIT_FOR_TASK_FINISH = os.getenv("JELLYFIN_WAIT_FOR_TASK_FINISH", "false").lower() in ("1", "true", "yes", "y")
TASK_WAIT_TIMEOUT_SECONDS = int(os.getenv("JELLYFIN_TASK_WAIT_TIMEOUT", "900"))  # 15 min


# =========================
# Helpers
# =========================

SCRIPT_DIR = Path(__file__).resolve().parent
NOW_TAG = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = SCRIPT_DIR / f"jellyfin_purge_collections_{NOW_TAG}.log"
BACKUP_PATH = SCRIPT_DIR / f"jellyfin_collections_backup_{NOW_TAG}.json"


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.info("Log file: %s", LOG_PATH)


def mb_authorization_header(api_key: str) -> str:
    # Formato recomendado: MediaBrowser key="value", ...
    # Token es lo único obligatorio, pero meter Client/Device ayuda a trazabilidad.
    def esc(v: str) -> str:
        return v.replace("\\", "\\\\").replace('"', '\\"')

    return (
        f'MediaBrowser Token="{esc(api_key)}", '
        f'Client="{esc(CLIENT_NAME)}", '
        f'Device="{esc(DEVICE_NAME)}", '
        f'DeviceId="{esc(DEVICE_ID)}", '
        f'Version="{esc(CLIENT_VERSION)}"'
    )


@dataclass
class JellyfinClient:
    base_url: str
    api_key: str
    verify_tls: bool
    timeout: int
    retries: int
    retry_base_sleep: float

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": mb_authorization_header(self.api_key),
                "Accept": "application/json",
            }
        )

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, json_body: Any = None) -> requests.Response:
        url = f"{self.base_url}{path}"
        last_err: Optional[Exception] = None

        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_body,
                    timeout=self.timeout,
                    verify=self.verify_tls,
                )

                # Reintentos en rate-limit o fallos transitorios
                if resp.status_code in (429, 500, 502, 503, 504):
                    sleep_s = self.retry_base_sleep * (2 ** (attempt - 1))
                    logging.warning(
                        "HTTP %s %s -> %s (attempt %d/%d). Retrying in %.1fs. Body: %s",
                        method,
                        path,
                        resp.status_code,
                        attempt,
                        self.retries,
                        sleep_s,
                        (resp.text or "")[:400].replace("\n", " "),
                    )
                    time.sleep(sleep_s)
                    continue

                # Errores no transitorios
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"HTTP {resp.status_code} on {method} {path}. "
                        f"Response: {(resp.text or '')[:800]}"
                    )

                return resp

            except Exception as e:
                last_err = e
                sleep_s = self.retry_base_sleep * (2 ** (attempt - 1))
                logging.warning(
                    "Request error on %s %s (attempt %d/%d): %s. Retrying in %.1fs",
                    method,
                    path,
                    attempt,
                    self.retries,
                    repr(e),
                    sleep_s,
                )
                time.sleep(sleep_s)

        raise RuntimeError(f"Request failed after {self.retries} retries: {method} {path}. Last error: {repr(last_err)}")

    def get_items_boxsets_paged(self) -> List[Dict[str, Any]]:
        all_items: List[Dict[str, Any]] = []
        start_index = 0

        while True:
            params = {
                "includeItemTypes": "BoxSet",
                "recursive": "true",
                "startIndex": str(start_index),
                "limit": str(PAGE_SIZE),
                "enableTotalRecordCount": "true",
            }
            r = self.request("GET", "/Items", params=params)
            data = r.json() if r.text else {}

            # Jellyfin puede devolver Items/TotalRecordCount (PascalCase) o items/totalRecordCount (camelCase)
            items = data.get("Items") or data.get("items") or []
            total = data.get("TotalRecordCount")
            if total is None:
                total = data.get("totalRecordCount")

            if not isinstance(items, list):
                raise RuntimeError(f"Unexpected /Items payload shape: {data}")

            all_items.extend(items)

            logging.info("Fetched BoxSets: %d (page startIndex=%d, got=%d, total=%s)", len(all_items), start_index, len(items), str(total))

            if len(items) < PAGE_SIZE:
                break

            start_index += len(items)

            if isinstance(total, int) and start_index >= total:
                break

        # Filtrado extra “por si acaso”
        filtered: List[Dict[str, Any]] = []
        for it in all_items:
            t = (it.get("Type") or it.get("type") or "").strip()
            if t == "BoxSet":
                filtered.append(it)

        return filtered

    def delete_item(self, item_id: str) -> None:
        self.request("DELETE", f"/Items/{item_id}")

    def get_scheduled_tasks(self) -> List[Dict[str, Any]]:
        r = self.request("GET", "/ScheduledTasks")
        data = r.json() if r.text else []
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected /ScheduledTasks payload: {data}")
        return data

    def start_task(self, task_id: str) -> None:
        self.request("POST", f"/ScheduledTasks/Running/{task_id}")

    def get_task(self, task_id: str) -> Dict[str, Any]:
        r = self.request("GET", f"/ScheduledTasks/{task_id}")
        data = r.json() if r.text else {}
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected /ScheduledTasks/{{id}} payload: {data}")
        return data


def pick_task_id(tasks: List[Dict[str, Any]], name_contains: str) -> Optional[Tuple[str, str]]:
    """
    Devuelve (taskId, taskName) buscando por substring case-insensitive en TaskInfo.Name.
    """
    needle = name_contains.strip().lower()
    for t in tasks:
        name = (t.get("Name") or "").strip()
        tid = (t.get("Id") or "").strip()
        if not name or not tid:
            continue
        if needle in name.lower():
            return tid, name
    return None


def is_task_running(task_info: Dict[str, Any]) -> bool:
    state = task_info.get("State") or task_info.get("state")
    # Suele ser "Running" / "Idle"
    return str(state).lower() == "running"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Borra TODAS las colecciones (BoxSet) de Jellyfin con API.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="No borra nada, solo lista y genera backup/log.")
    parser.add_argument("--yes", action="store_true", help="No pregunta confirmación (modo automático).")
    parser.add_argument("--verbose", action="store_true", help="Logs más detallados.")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if API_KEY.strip() in ("", "PEGA_AQUI_TU_API_KEY"):
        logging.error("API_KEY no configurada. Pon JELLYFIN_API_KEY en env o edita API_KEY arriba.")
        return 2

    jf = JellyfinClient(
        base_url=JELLYFIN_URL,
        api_key=API_KEY,
        verify_tls=VERIFY_TLS,
        timeout=TIMEOUT_SECONDS,
        retries=RETRIES,
        retry_base_sleep=RETRY_BASE_SLEEP,
    )

    # 1) Listar BoxSets
    logging.info("Jellyfin URL: %s", JELLYFIN_URL)
    logging.info("VERIFY_TLS=%s | TIMEOUT=%ss | PAGE_SIZE=%d | DRY_RUN=%s", VERIFY_TLS, TIMEOUT_SECONDS, PAGE_SIZE, args.dry_run)

    boxsets = jf.get_items_boxsets_paged()
    if not boxsets:
        logging.info("No hay colecciones (BoxSet) que borrar. Fin.")
        return 0

    # Normaliza a {Id, Name, Type} para backup
    to_backup: List[Dict[str, Any]] = []
    ids: List[str] = []
    for it in boxsets:
        item_id = it.get("Id") or it.get("id")
        name = it.get("Name") or it.get("name")
        t = it.get("Type") or it.get("type")
        if not item_id:
            continue
        ids.append(str(item_id))
        to_backup.append({"Id": str(item_id), "Name": name, "Type": t})

    # 2) Backup
    BACKUP_PATH.write_text(json.dumps(to_backup, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Backup escrito: %s (colecciones=%d)", BACKUP_PATH, len(ids))

    # 3) Confirmación
    logging.info("Se van a borrar %d colecciones (BoxSet).", len(ids))
    if not args.yes and not args.dry_run:
        print(f"\nVas a BORRAR {len(ids)} colecciones (BoxSet) en: {JELLYFIN_URL}")
        print(f"Backup: {BACKUP_PATH}")
        confirm = input("Escribe BORRAR para continuar: ").strip()
        if confirm != "BORRAR":
            logging.warning("Cancelado por el usuario.")
            return 1

    # 4) Borrado
    if args.dry_run:
        logging.info("DRY-RUN activo. No se borra nada.")
    else:
        deleted = 0
        for i, item_id in enumerate(ids, start=1):
            try:
                jf.delete_item(item_id)
                deleted += 1
                logging.info("Deleted (%d/%d): %s", i, len(ids), item_id)
            except Exception as e:
                logging.error("FAILED delete (%d/%d) id=%s: %s", i, len(ids), item_id, repr(e))
        logging.info("Borrado terminado. deleted=%d / total=%d", deleted, len(ids))

    # 5) Verificación final (que ya no quedan BoxSet)
    remaining = jf.get_items_boxsets_paged()
    logging.info("Verificación: BoxSets restantes=%d", len(remaining))

    # 6) (Opcional) Lanzar tarea de mantenimiento “Clean up collections and playlists”
    if TRIGGER_CLEANUP_TASK and not args.dry_run:
        try:
            tasks = jf.get_scheduled_tasks()
            pick = pick_task_id(tasks, "clean up collections and playlists")
            if pick:
                task_id, task_name = pick
                logging.info('Lanzando tarea: "%s" (Id=%s)', task_name, task_id)
                jf.start_task(task_id)

                if WAIT_FOR_TASK_FINISH:
                    t0 = time.time()
                    while True:
                        info = jf.get_task(task_id)
                        if not is_task_running(info):
                            logging.info('Tarea finalizada: "%s"', task_name)
                            break
                        if time.time() - t0 > TASK_WAIT_TIMEOUT_SECONDS:
                            logging.warning("Timeout esperando a que termine la tarea (%ss).", TASK_WAIT_TIMEOUT_SECONDS)
                            break
                        time.sleep(2.0)
            else:
                logging.warning('No encontré la tarea "Clean up collections and playlists" en /ScheduledTasks.')
        except Exception as e:
            logging.warning("No se pudo lanzar/verificar la tarea de limpieza: %s", repr(e))

    logging.info("FIN. Log=%s | Backup=%s", LOG_PATH, BACKUP_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

import requests


# ----------------------------
# Defaults
# ----------------------------

DEFAULT_CONF_DIR = r"E:\Docker_folders\_mullvadvpn-servers"
DEFAULT_DB_FILE = "vpn_autotest.sqlite"
DEFAULT_LOG_FILE = "vpn_autotest.log"

DEFAULT_GLUETUN_SERVICE = "gluetun-vpn-test"
DEFAULT_DISPATCHARR_SERVICE = "gluetun-dispatcharr-test"

DEFAULT_DISPATCHARR_BASE = "http://127.0.0.1:9191"

DEFAULT_STATUS_POLL_S = 2.0

QUALITY_TARGET_Mbps = {"SD": 3.0, "HD": 6.0, "FHD": 10.0}

CURL_IMAGE = "curlimages/curl:8.5.0"


# ----------------------------
# Models
# ----------------------------

@dataclasses.dataclass
class StreamTestResult:
    ok: bool
    duration_s: float
    samples_total: int
    samples_present: int
    presence_ratio: float
    current_speed_median: Optional[float]
    buffering_ratio: Optional[float]
    bitrate_mbps_est: Optional[float]
    derived: Dict[str, Any]
    status_samples: List[Dict[str, Any]]
    error: Optional[str] = None


@dataclasses.dataclass
class SpeedTestResult:
    ok: bool
    download_mbps: Optional[float]
    upload_mbps: Optional[float]
    provider: Optional[str]
    raw: Dict[str, Any]
    error: Optional[str] = None


@dataclasses.dataclass
class VpnIdentity:
    ok: bool
    public_ip: Optional[str]
    raw: Dict[str, Any]
    error: Optional[str] = None


@dataclasses.dataclass
class RunResult:
    conf_name: str
    started_at: str
    ended_at: str
    mode_used: str
    vpn: VpnIdentity
    dispatcharr_ready: bool
    stream_url: str
    quality: str
    stream: StreamTestResult
    speed: SpeedTestResult
    score_total: int
    score_stream: int
    score_speed: int
    quarantined: bool
    quarantine_reason: Optional[str]


# ----------------------------
# Helpers
# ----------------------------

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def setup_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("vpn_autotest")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)

    fh = RotatingFileHandler(str(log_path), maxBytes=3_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)

    logger.handlers.clear()
    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger


def run_cmd(logger: logging.Logger, args: List[str], cwd: Path, timeout_s: Optional[int] = None, check: bool = True) -> subprocess.CompletedProcess:
    logger.info("CMD: %s", " ".join(args))
    cp = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s)
    if cp.stdout.strip():
        logger.info("STDOUT: %s", cp.stdout.strip()[:2000])
    if cp.stderr.strip():
        logger.info("STDERR: %s", cp.stderr.strip()[:2000])
    if check and cp.returncode != 0:
        raise RuntimeError(f"Command failed ({cp.returncode}): {' '.join(args)}")
    return cp


def detect_compose_file(project_dir: Path) -> Path:
    for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        p = project_dir / name
        if p.exists():
            return p
    raise FileNotFoundError("No compose file found (docker-compose.yml/compose.yml).")


def docker_compose(logger: logging.Logger, project_dir: Path, compose_file: Path, compose_args: List[str], timeout_s: Optional[int] = None, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-f", str(compose_file)] + compose_args
    return run_cmd(logger, cmd, cwd=project_dir, timeout_s=timeout_s, check=check)


def wait_container_stable(logger: logging.Logger, container_name: str, timeout_s: int = 90) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            cp = subprocess.run(
                ["docker", "inspect", "-f",
                 "{{.State.Running}} {{.State.Restarting}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                 container_name],
                capture_output=True, text=True, timeout=10
            )
            if cp.returncode == 0:
                parts = cp.stdout.strip().split()
                running = (len(parts) >= 1 and parts[0] == "true")
                restarting = (len(parts) >= 2 and parts[1] == "true")
                health = parts[2] if len(parts) >= 3 else ""
                if running and (not restarting) and (health in ("", "healthy")):
                    return True
        except Exception:
            pass
        time.sleep(2)
    logger.info("Container %s no está estable tras %ss", container_name, timeout_s)
    return False


def parse_channel_uuid_from_stream_url(stream_url: str) -> Optional[str]:
    # UUID que aparece en /proxy/ts/stream/<uuid>
    m = re.search(r"/proxy/ts/stream/([0-9a-fA-F-]{36})", stream_url)
    return m.group(1) if m else None


def parse_number(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    m = re.search(r"(-?\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def parse_bitrate_to_kbps(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    num = float(m.group(1))
    if "mb" in s.lower():
        return num * 1000.0
    return num


# ----------------------------
# wg0.conf management
# ----------------------------

def runtime_dir(project_dir: Path) -> Path:
    return project_dir / "_gluetun_runtime"


def wg0_path(project_dir: Path) -> Path:
    return runtime_dir(project_dir) / "wg0.conf"


def conf_has_endpoint(conf_file: Path) -> bool:
    try:
        txt = conf_file.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^\s*Endpoint\s*=\s*(\S+)\s*$", txt, flags=re.MULTILINE)
        return bool(m and m.group(1))
    except Exception:
        return False


def ensure_wg0_exists_at_start(logger: logging.Logger, project_dir: Path, bootstrap_conf: Path) -> None:
    rd = runtime_dir(project_dir)
    rd.mkdir(parents=True, exist_ok=True)
    wg0 = wg0_path(project_dir)
    if wg0.exists():
        return
    shutil.copyfile(bootstrap_conf, wg0)
    logger.info("BOOTSTRAP: no existía wg0.conf -> copiado %s a %s", bootstrap_conf.name, str(wg0))
    if not conf_has_endpoint(wg0):
        logger.info("AVISO: wg0.conf bootstrap no parece contener 'Endpoint = ...' (podría fallar).")


def set_wg0_from_conf(logger: logging.Logger, project_dir: Path, conf_path: Path) -> None:
    rd = runtime_dir(project_dir)
    rd.mkdir(parents=True, exist_ok=True)
    dst = wg0_path(project_dir)
    shutil.copyfile(conf_path, dst)
    logger.info("wg0.conf actualizado: %s -> %s", conf_path.name, str(dst))
    if not conf_has_endpoint(dst):
        logger.info("AVISO: %s no parece contener 'Endpoint = ...' (Gluetun puede fallar).", conf_path.name)


# ----------------------------
# SQLite
# ----------------------------

def db_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def db_init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS servers (
            conf_name TEXT PRIMARY KEY,
            conf_path TEXT NOT NULL,
            quarantined INTEGER NOT NULL DEFAULT 0,
            quarantine_reason TEXT,
            quarantine_at TEXT,
            last_test_at TEXT,
            last_score_total INTEGER,
            last_score_stream INTEGER,
            last_score_speed INTEGER
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conf_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            mode_used TEXT NOT NULL,
            vpn_public_ip TEXT,
            vpn_raw_json TEXT,
            dispatcharr_ready INTEGER NOT NULL,
            stream_url TEXT NOT NULL,
            quality TEXT NOT NULL,
            stream_ok INTEGER NOT NULL,
            stream_json TEXT,
            speed_ok INTEGER NOT NULL,
            speed_json TEXT,
            score_total INTEGER NOT NULL,
            score_stream INTEGER NOT NULL,
            score_speed INTEGER NOT NULL,
            quarantined INTEGER NOT NULL,
            quarantine_reason TEXT
        );
        """
    )
    conn.commit()


def db_upsert_server(conn: sqlite3.Connection, conf_name: str, conf_path: str) -> None:
    conn.execute(
        """
        INSERT INTO servers(conf_name, conf_path)
        VALUES(?, ?)
        ON CONFLICT(conf_name) DO UPDATE SET conf_path=excluded.conf_path
        """,
        (conf_name, conf_path),
    )
    conn.commit()


def db_mark_server(conn: sqlite3.Connection, conf_name: str, last_test_at: str,
                   scores: Tuple[int, int, int], quarantined: bool, quarantine_reason: Optional[str]) -> None:
    score_total, score_stream, score_speed = scores
    if quarantined:
        conn.execute(
            """
            UPDATE servers
            SET quarantined=1, quarantine_reason=?, quarantine_at=?,
                last_test_at=?, last_score_total=?, last_score_stream=?, last_score_speed=?
            WHERE conf_name=?
            """,
            (quarantine_reason, now_iso(), last_test_at, score_total, score_stream, score_speed, conf_name),
        )
    else:
        conn.execute(
            """
            UPDATE servers
            SET quarantined=0, quarantine_reason=NULL, quarantine_at=NULL,
                last_test_at=?, last_score_total=?, last_score_stream=?, last_score_speed=?
            WHERE conf_name=?
            """,
            (last_test_at, score_total, score_stream, score_speed, conf_name),
        )
    conn.commit()


def db_insert_run(conn: sqlite3.Connection, rr: RunResult) -> int:
    cur = conn.execute(
        """
        INSERT INTO runs(
            conf_name, started_at, ended_at, mode_used,
            vpn_public_ip, vpn_raw_json,
            dispatcharr_ready,
            stream_url, quality,
            stream_ok, stream_json,
            speed_ok, speed_json,
            score_total, score_stream, score_speed,
            quarantined, quarantine_reason
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            rr.conf_name, rr.started_at, rr.ended_at, rr.mode_used,
            rr.vpn.public_ip, json.dumps(rr.vpn.raw, ensure_ascii=False),
            1 if rr.dispatcharr_ready else 0,
            rr.stream_url, rr.quality,
            1 if rr.stream.ok else 0, json.dumps(dataclasses.asdict(rr.stream), ensure_ascii=False),
            1 if rr.speed.ok else 0, json.dumps(dataclasses.asdict(rr.speed), ensure_ascii=False),
            rr.score_total, rr.score_stream, rr.score_speed,
            1 if rr.quarantined else 0, rr.quarantine_reason
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


# ----------------------------
# VPN identity (netns)
# ----------------------------

def vpn_identity_via_netns(logger: logging.Logger, project_dir: Path, gluetun_container: str) -> VpnIdentity:
    try:
        cmd = [
            "docker", "run", "--rm",
            "--network", f"container:{gluetun_container}",
            CURL_IMAGE,
            "-sS", "--max-time", "10",
            "https://api.ipify.org?format=json",
        ]
        cp = run_cmd(logger, cmd, cwd=project_dir, timeout_s=20, check=True)
        data = json.loads(cp.stdout.strip())
        ip = (data.get("ip") or "").strip()
        if not ip:
            return VpnIdentity(ok=False, public_ip=None, raw={"ipify": data}, error="empty_ipify_ip")
        return VpnIdentity(ok=True, public_ip=ip, raw={"ipify": data})
    except Exception as e:
        return VpnIdentity(ok=False, public_ip=None, raw={"ipify": None}, error=str(e))


# ----------------------------
# Dispatcharr client (auth for /proxy/ts/status)
# ----------------------------

class DispatcharrClient:
    def __init__(self, base_url: str, username: Optional[str], password: Optional[str], logger: logging.Logger):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.logger = logger
        self.access_token: Optional[str] = None
        self.sess = requests.Session()

    def _auth_headers(self) -> Dict[str, str]:
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    def login(self) -> bool:
        if not self.username or not self.password:
            return False
        try:
            # En dispatcharr_sensor usan /api/accounts/token/ para obtener access JWT :contentReference[oaicite:3]{index=3}
            r = self.sess.post(
                f"{self.base_url}/api/accounts/token/",
                json={"username": self.username, "password": self.password},
                timeout=(5.0, 10.0),
            )
            if r.status_code >= 400:
                self.logger.info("Auth FAIL: %s %s", r.status_code, r.text[:200])
                return False
            data = r.json()
            self.access_token = data.get("access")
            ok = bool(self.access_token)
            self.logger.info("Auth %s (token=%s)", "OK" if ok else "FAIL", "present" if self.access_token else "missing")
            return ok
        except Exception as e:
            self.logger.info("Auth EXC: %s", str(e))
            return False

    def get_status(self, timeout: Tuple[float, float]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        Devuelve (json, meta). meta incluye http_code/content_type/snippet.
        Si 401, intenta login (si hay user/pass) y reintenta 1 vez.
        """
        url = f"{self.base_url}/proxy/ts/status"
        meta: Dict[str, Any] = {}
        try:
            r = self.sess.get(url, headers=self._auth_headers(), timeout=timeout, allow_redirects=False)
            meta.update({
                "http_code": r.status_code,
                "content_type": r.headers.get("content-type", ""),
            })

            if r.status_code == 401 and (self.username and self.password):
                self.logger.info("Status 401 -> re-auth + retry")
                if self.login():
                    r = self.sess.get(url, headers=self._auth_headers(), timeout=timeout, allow_redirects=False)
                    meta.update({
                        "http_code": r.status_code,
                        "content_type": r.headers.get("content-type", ""),
                    })

            # Si no parece JSON, devolvemos None y snippet para debug
            ct = (meta.get("content_type") or "").lower()
            if "application/json" not in ct:
                meta["snippet"] = (r.text or "")[:200]
                return None, meta

            return r.json(), meta
        except Exception as e:
            meta["error"] = str(e)
            return None, meta


def wait_for_dispatcharr(base: str, timeout_s: int = 120) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(base.rstrip("/") + "/", timeout=(3.0, 5.0), allow_redirects=False)
            if r.status_code in (200, 301, 302, 401, 403):
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


# ----------------------------
# Kick stream (only to stimulate stats)
# ----------------------------

def kickoff_stream_background(stream_url: str, connect_timeout: float, read_timeout: float, keepalive_s: int, logger: logging.Logger) -> threading.Event:
    stop_event = threading.Event()

    def _run() -> None:
        deadline = time.time() + keepalive_s
        timeout = (connect_timeout, read_timeout)
        while time.time() < deadline and not stop_event.is_set():
            r = None
            try:
                r = requests.get(stream_url, stream=True, timeout=timeout)
                it = r.iter_content(chunk_size=64 * 1024)
                t_inner = time.time() + 8.0
                while time.time() < t_inner and not stop_event.is_set():
                    try:
                        _ = next(it)
                    except (StopIteration, requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                        break
                    except Exception:
                        break
                    time.sleep(0.05)
            except Exception:
                pass
            finally:
                try:
                    if r is not None:
                        r.close()
                except Exception:
                    pass
            time.sleep(0.5)
        logger.info("Kickoff stream thread finished")

    threading.Thread(target=_run, name="kickoff_stream", daemon=True).start()
    return stop_event


# ----------------------------
# Status parsing + selection
# ----------------------------

def _iter_string_values(d: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for v in d.values():
        if isinstance(v, str):
            out.append(v)
    return out


def pick_active_entry(status_json: Dict[str, Any], token_uuid: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve un item de 'channels' que corresponda a ESTE stream.
    Heurística:
      1) match exacto por channel_id/id/uuid...
      2) match por "contiene token" en cualquier string field
      3) si solo hay 1 stream activo, lo devolvemos
    """
    chans = status_json.get("channels")
    if not isinstance(chans, list) or not chans:
        return None

    t = token_uuid.lower()

    # 1) match exacto por keys típicas
    for ch in chans:
        if not isinstance(ch, dict):
            continue
        for key in ("channel_id", "id", "uuid", "channel_uuid", "stream_uuid", "token"):
            v = ch.get(key)
            if isinstance(v, str) and v.lower() == t:
                return ch

    # 2) match por substring en cualquier string field
    for ch in chans:
        if not isinstance(ch, dict):
            continue
        for s in _iter_string_values(ch):
            if t in s.lower():
                return ch

    # 3) si solo hay uno
    if len(chans) == 1 and isinstance(chans[0], dict):
        return chans[0]

    return None


# ----------------------------
# Status-based stream test
# ----------------------------

def status_based_stream_test(
    logger: logging.Logger,
    da: DispatcharrClient,
    stream_url: str,
    quality: str,
    duration_s: int,
    poll_s: float,
    status_timeout: Tuple[float, float],
    buffering_threshold: float,
    min_presence_ratio: float,
) -> StreamTestResult:
    token_uuid = parse_channel_uuid_from_stream_url(stream_url)
    if not token_uuid:
        return StreamTestResult(
            ok=False, duration_s=0.0, samples_total=0, samples_present=0,
            presence_ratio=0.0, current_speed_median=None, buffering_ratio=None,
            bitrate_mbps_est=None, derived={}, status_samples=[],
            error="cannot_parse_stream_uuid"
        )

    t0 = time.time()
    total = 0
    present = 0
    speed_vals: List[float] = []
    bitrate_kbps_vals: List[float] = []
    buffer_events = 0
    samples: List[Dict[str, Any]] = []
    derived: Dict[str, Any] = {}

    meta_first: Optional[Dict[str, Any]] = None

    while True:
        if (time.time() - t0) >= duration_s:
            break

        total += 1
        st, meta = da.get_status(timeout=status_timeout)
        if meta_first is None:
            meta_first = meta

        if isinstance(st, dict):
            ch = pick_active_entry(st, token_uuid)
        else:
            ch = None

        if isinstance(ch, dict):
            present += 1
            snap = dict(ch)
            snap["_ts"] = now_iso()
            samples.append(snap)

            # current_speed: suele existir si el stream está activo (monitoriza buffering) :contentReference[oaicite:4]{index=4}
            cs = parse_number(ch.get("current_speed") or ch.get("speed") or ch.get("currentSpeed"))
            if cs is not None:
                speed_vals.append(cs)
                if cs < buffering_threshold:
                    buffer_events += 1

            # bitrate
            bk = parse_bitrate_to_kbps(ch.get("avg_bitrate") or ch.get("average_bitrate") or ch.get("avgBitrate"))
            if bk is None:
                bk = parse_bitrate_to_kbps(ch.get("current_bitrate") or ch.get("bitrate") or ch.get("currentBitrate"))
            if bk is not None:
                bitrate_kbps_vals.append(bk)

            for k in ("resolution", "source_fps", "video_codec", "audio_codec", "stream_type"):
                if ch.get(k) and k not in derived:
                    derived[k] = ch.get(k)

        time.sleep(poll_s)

    presence_ratio = (present / total) if total else 0.0
    speed_median = median(speed_vals) if speed_vals else None
    buffering_ratio = (buffer_events / len(speed_vals)) if speed_vals else None
    bitrate_mbps_est = (median(bitrate_kbps_vals) / 1000.0) if bitrate_kbps_vals else None

    derived.update({
        "presence_ratio": presence_ratio,
        "buffering_threshold": buffering_threshold,
        "buffer_events": buffer_events,
        "speed_samples": len(speed_vals),
        "bitrate_samples": len(bitrate_kbps_vals),
        "bitrate_mbps_est": bitrate_mbps_est,
        "quality_target_mbps": QUALITY_TARGET_Mbps.get(quality.upper()),
        "status_meta_first": meta_first or {},
    })

    ok = (presence_ratio >= min_presence_ratio) and (speed_vals or bitrate_kbps_vals)

    err = None
    if not ok:
        if meta_first and meta_first.get("http_code") and meta_first.get("http_code") != 200:
            err = f"status_http_{meta_first.get('http_code')}"
        elif meta_first and meta_first.get("content_type") and "application/json" not in str(meta_first.get("content_type")).lower():
            err = f"status_not_json:{meta_first.get('content_type')}"
        elif presence_ratio < min_presence_ratio:
            err = f"low_presence_ratio:{presence_ratio:.2f}"
        else:
            err = "no_speed_or_bitrate_samples"

    return StreamTestResult(
        ok=ok,
        duration_s=float(time.time() - t0),
        samples_total=total,
        samples_present=present,
        presence_ratio=presence_ratio,
        current_speed_median=speed_median,
        buffering_ratio=buffering_ratio,
        bitrate_mbps_est=bitrate_mbps_est,
        derived=derived,
        status_samples=samples,
        error=err
    )


# ----------------------------
# Scoring (stream-first)
# ----------------------------

def compute_scores_status_first(stream: StreamTestResult, quality: str) -> Tuple[int, int, int, bool, Optional[str]]:
    if not stream.ok:
        return 0, 0, 0, True, f"stream_failed: {stream.error or 'unknown'}"

    target = QUALITY_TARGET_Mbps.get(quality.upper(), 6.0)

    # bitrate factor
    if stream.bitrate_mbps_est is not None:
        bitrate_factor = min(max(stream.bitrate_mbps_est / max(0.1, target), 0.0), 1.3)
    else:
        bitrate_factor = 0.6

    # speed factor: current_speed ~ 1.0 => ok; <1 => buffering
    if stream.current_speed_median is not None:
        speed_factor = min(max(stream.current_speed_median, 0.0), 1.2)
        speed_factor = min(speed_factor, 1.0)
    else:
        speed_factor = 0.7

    if stream.buffering_ratio is not None:
        buffering_penalty = max(0.0, 1.0 - min(0.9, stream.buffering_ratio * 1.2))
    else:
        buffering_penalty = 0.8

    presence_penalty = max(0.0, min(1.0, stream.presence_ratio))

    stream_score = int(900 * bitrate_factor * speed_factor * buffering_penalty * presence_penalty)
    stream_score = max(0, min(900, stream_score))

    total = min(1000, stream_score)
    quarantined = stream_score < 150
    reason = "stream_score_too_low" if quarantined else None
    return total, stream_score, 0, quarantined, reason


# ----------------------------
# Conf selection
# ----------------------------

def list_conf_files(conf_dir: Path) -> List[Path]:
    return sorted(conf_dir.glob("*.conf"), key=lambda x: x.name.lower())


def normalize_conf_name(s: str) -> str:
    s = s.strip()
    return s[:-5] if s.lower().endswith(".conf") else s


def find_conf_by_name(conf_files: List[Path], name_or_file: str) -> Optional[Path]:
    wanted = normalize_conf_name(name_or_file).lower()
    for p in conf_files:
        if p.stem.lower() == wanted or p.name.lower() == name_or_file.lower():
            return p
    return None


# ----------------------------
# Main
# ----------------------------

def main() -> int:
    ap = argparse.ArgumentParser()

    ap.add_argument("--conf-dir", default=DEFAULT_CONF_DIR)
    ap.add_argument("--only-conf", required=True, help="ej: fi-hel-wg-001 o fi-hel-wg-001.conf")
    ap.add_argument("--reset-db", action="store_true")

    ap.add_argument("--dispatcharr-base", default=DEFAULT_DISPATCHARR_BASE)
    ap.add_argument("--stream-url", required=True)
    ap.add_argument("--quality", default="HD", choices=["SD", "HD", "FHD"])

    # Auth for /proxy/ts/status (Bearer token)
    ap.add_argument("--da-user", default=None, help="usuario Dispatcharr (para stats/status)")
    ap.add_argument("--da-pass", default=None, help="password Dispatcharr (para stats/status)")

    ap.add_argument("--gluetun-service", default=DEFAULT_GLUETUN_SERVICE)
    ap.add_argument("--dispatcharr-service", default=DEFAULT_DISPATCHARR_SERVICE)

    # Status test tuning
    ap.add_argument("--status-duration", type=int, default=60)
    ap.add_argument("--status-poll", type=float, default=DEFAULT_STATUS_POLL_S)
    ap.add_argument("--status-connect-timeout", type=float, default=2.0)
    ap.add_argument("--status-read-timeout", type=float, default=2.0)
    ap.add_argument("--buffering-threshold", type=float, default=1.0)
    ap.add_argument("--min-presence-ratio", type=float, default=0.50)

    # Kick stream
    ap.add_argument("--kick-stream-seconds", type=int, default=90)
    ap.add_argument("--kick-connect-timeout", type=float, default=8.0)
    ap.add_argument("--kick-read-timeout", type=float, default=5.0)

    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    project_dir = Path(__file__).resolve().parent
    compose_file = detect_compose_file(project_dir)
    logger = setup_logging(project_dir / DEFAULT_LOG_FILE)

    db_path = project_dir / DEFAULT_DB_FILE
    if args.reset_db and db_path.exists():
        db_path.unlink()
        logger.info("DB RESET: borrado %s", str(db_path))

    conn = db_connect(db_path)
    db_init(conn)

    conf_dir = Path(args.conf_dir)
    conf_files = list_conf_files(conf_dir)
    if not conf_files:
        logger.error("No hay .conf en %s", str(conf_dir))
        return 2

    conf_path = find_conf_by_name(conf_files, args.only_conf)
    if not conf_path:
        logger.error("--only-conf '%s' no encontrado en %s", args.only_conf, str(conf_dir))
        return 2

    logger.info("ONLY-CONF: forzando servidor %s", conf_path.name)

    ensure_wg0_exists_at_start(logger, project_dir, conf_path)
    set_wg0_from_conf(logger, project_dir, conf_path)

    conf_name = conf_path.stem
    db_upsert_server(conn, conf_name, str(conf_path))

    started = now_iso()
    logger.info("=== TEST START | %s | %s ===", conf_name, str(conf_path))

    # stop dispatcharr first
    with contextlib.suppress(Exception):
        docker_compose(logger, project_dir, compose_file, ["stop", args.dispatcharr_service], check=False)

    # recreate gluetun
    docker_compose(logger, project_dir, compose_file, ["up", "-d", "--force-recreate", args.gluetun_service])
    if not wait_container_stable(logger, args.gluetun_service, timeout_s=90):
        raise RuntimeError("gluetun_not_stable")

    vpn = vpn_identity_via_netns(logger, project_dir, args.gluetun_service)
    if not vpn.ok:
        ended = now_iso()
        rr = RunResult(
            conf_name=conf_name, started_at=started, ended_at=ended, mode_used="RECREATE",
            vpn=vpn, dispatcharr_ready=False, stream_url=args.stream_url, quality=args.quality,
            stream=StreamTestResult(False, 0.0, 0, 0, 0.0, None, None, None, {}, [], "vpn_no_public_ip"),
            speed=SpeedTestResult(False, None, None, None, {}, "vpn_no_public_ip"),
            score_total=0, score_stream=0, score_speed=0,
            quarantined=True, quarantine_reason="vpn_no_public_ip"
        )
        db_insert_run(conn, rr)
        db_mark_server(conn, conf_name, ended, (0, 0, 0), True, rr.quarantine_reason)
        return 0

    logger.info("VPN OK | public_ip=%s | mode=RECREATE", vpn.public_ip)

    # start dispatcharr
    docker_compose(logger, project_dir, compose_file, ["up", "-d", args.dispatcharr_service])
    if not wait_for_dispatcharr(args.dispatcharr_base, timeout_s=120):
        ended = now_iso()
        rr = RunResult(
            conf_name=conf_name, started_at=started, ended_at=ended, mode_used="RECREATE",
            vpn=vpn, dispatcharr_ready=False, stream_url=args.stream_url, quality=args.quality,
            stream=StreamTestResult(False, 0.0, 0, 0, 0.0, None, None, None, {}, [], "dispatcharr_not_ready"),
            speed=SpeedTestResult(False, None, None, None, {}, "dispatcharr_not_ready"),
            score_total=0, score_stream=0, score_speed=0,
            quarantined=True, quarantine_reason="dispatcharr_not_ready"
        )
        db_insert_run(conn, rr)
        db_mark_server(conn, conf_name, ended, (0, 0, 0), True, rr.quarantine_reason)
        return 0

    logger.info("Dispatcharr READY at %s", args.dispatcharr_base)

    # Dispatcharr API client (auth optional but usually needed for /proxy/ts/status) :contentReference[oaicite:5]{index=5}
    da = DispatcharrClient(args.dispatcharr_base, args.da_user, args.da_pass, logger)
    if args.da_user and args.da_pass:
        da.login()

    # Kick the stream to stimulate stats (Proxy monitors buffering, switches streams, etc.) :contentReference[oaicite:6]{index=6}
    stop_kick = kickoff_stream_background(
        stream_url=args.stream_url,
        connect_timeout=args.kick_connect_timeout,
        read_timeout=args.kick_read_timeout,
        keepalive_s=args.kick_stream_seconds,
        logger=logger,
    )

    stream_res = status_based_stream_test(
        logger=logger,
        da=da,
        stream_url=args.stream_url,
        quality=args.quality,
        duration_s=args.status_duration,
        poll_s=args.status_poll,
        status_timeout=(args.status_connect_timeout, args.status_read_timeout),
        buffering_threshold=args.buffering_threshold,
        min_presence_ratio=args.min_presence_ratio,
    )

    stop_kick.set()

    logger.info(
        "Status test | ok=%s | presence=%.2f (%d/%d) | speed_med=%s | buf_ratio=%s | bitrate_est=%s Mbps | err=%s | meta=%s",
        stream_res.ok,
        stream_res.presence_ratio, stream_res.samples_present, stream_res.samples_total,
        f"{stream_res.current_speed_median:.2f}" if stream_res.current_speed_median is not None else "None",
        f"{stream_res.buffering_ratio:.2f}" if stream_res.buffering_ratio is not None else "None",
        f"{stream_res.bitrate_mbps_est:.2f}" if stream_res.bitrate_mbps_est is not None else "None",
        stream_res.error,
        (stream_res.derived or {}).get("status_meta_first", {}),
    )

    score_total, score_stream, score_speed, quarantined, q_reason = compute_scores_status_first(stream_res, args.quality)

    ended = now_iso()
    rr = RunResult(
        conf_name=conf_name, started_at=started, ended_at=ended, mode_used="RECREATE",
        vpn=vpn, dispatcharr_ready=True,
        stream_url=args.stream_url, quality=args.quality,
        stream=stream_res,
        speed=SpeedTestResult(ok=False, download_mbps=None, upload_mbps=None, provider=None, raw={}, error="disabled"),
        score_total=score_total, score_stream=score_stream, score_speed=score_speed,
        quarantined=quarantined, quarantine_reason=q_reason,
    )

    run_id = db_insert_run(conn, rr)
    db_mark_server(conn, conf_name, ended, (score_total, score_stream, score_speed), quarantined, q_reason)

    logger.info(
        "DB WRITE | run_id=%d | conf=%s | score=%d (stream=%d) | quarantined=%s reason=%s",
        run_id, conf_name, score_total, score_stream, quarantined, q_reason
    )

    with contextlib.suppress(Exception):
        docker_compose(logger, project_dir, compose_file, ["stop", args.dispatcharr_service], check=False)

    logger.info("=== TEST END | %s | score=%d | quarantined=%s ===", conf_name, score_total, quarantined)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
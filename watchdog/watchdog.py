import os
import sys
import time
import json
import datetime as dt
from urllib.parse import quote

import requests

try:
    import requests_unixsocket
except Exception:
    requests_unixsocket = None


def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"{now_str()} | [watchdog] {msg}", flush=True)


def getenv_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def getenv_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


def normalize_docker_host(raw: str) -> tuple[str, str]:
    """
    Returns (mode, base_url)
    mode: "unix" or "http"
    """
    raw = (raw or "").strip()
    if not raw:
        raw = "unix:///var/run/docker.sock"

    if raw.startswith("unix://"):
        sock_path = raw[len("unix://") :]
        # requests-unixsocket wants http+unix + urlencoded path
        return "unix", f"http+unix://{quote(sock_path, safe='')}"

    if raw.startswith("tcp://"):
        raw = "http://" + raw[len("tcp://") :]

    if raw.startswith("http://") or raw.startswith("https://"):
        return "http", raw.rstrip("/")

    # fallback: treat as http host:port
    return "http", ("http://" + raw).rstrip("/")


class DockerAPI:
    def __init__(self, docker_host: str, timeout: int = 5):
        self.mode, self.base = normalize_docker_host(docker_host)
        self.timeout = timeout

        if self.mode == "unix":
            if requests_unixsocket is None:
                raise RuntimeError("requests-unixsocket not installed but DOCKER_HOST is unix://")
            self.session = requests_unixsocket.Session()
        else:
            self.session = requests.Session()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base + path

    def get_json(self, path: str):
        r = self.session.get(self._url(path), timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def post(self, path: str):
        r = self.session.post(self._url(path), timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:300]}")
        return r

    def container_inspect(self, name: str):
        return self.get_json(f"/containers/{name}/json")

    def container_restart(self, name: str, timeout_s: int = 10):
        return self.post(f"/containers/{name}/restart?t={timeout_s}")

    def container_start(self, name: str):
        return self.post(f"/containers/{name}/start")

    def container_logs_tail(self, name: str, tail: int = 60):
        url = self._url(f"/containers/{name}/logs?stdout=1&stderr=1&tail={tail}")
        r = self.session.get(url, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"GET logs {name} -> {r.status_code}: {r.text[:200]}")
        try:
            return r.content.decode("utf-8", errors="replace")
        except Exception:
            return str(r.content[:500])


def summarize_health(health: dict) -> dict:
    if not health:
        return {"Status": "none", "FailingStreak": None, "Last": None}

    logs = health.get("Log") or []
    last = logs[-1] if logs else None
    last_summary = None
    if last:
        last_summary = {
            "ExitCode": last.get("ExitCode"),
            "Start": last.get("Start"),
            "End": last.get("End"),
            "Output": (last.get("Output") or "").strip()[:300],
        }

    return {
        "Status": health.get("Status"),
        "FailingStreak": health.get("FailingStreak"),
        "Last": last_summary,
        "LogCount": len(logs),
    }


def restart_targets(api: DockerAPI, targets: list[str], timeout_s: int, log_tail: int, vpn_container: str):
    log(f"reiniciando: {','.join(targets) if targets else '(none)'}")
    for name in targets:
        if not name:
            continue
        try:
            api.container_restart(name, timeout_s=timeout_s)
            log(f"restart OK: {name}")
        except Exception as e:
            log(f"restart FAIL: {name}: {str(e)[:200]}")
            try:
                api.container_start(name)
                log(f"start OK (fallback): {name}")
            except Exception as e2:
                log(f"start FAIL: {name}: {str(e2)[:200]}")

    # tail logs del VPN para diagnóstico
    try:
        tail = api.container_logs_tail(vpn_container, tail=log_tail)
        log(f"--- {vpn_container} logs tail({log_tail}) ---\n{tail}\n--- end tail ---")
    except Exception as ex:
        log(f"could not fetch {vpn_container} logs: {ex}")


def main() -> int:
    vpn_container = os.environ.get("VPN_CONTAINER", "vpn-stable").strip()
    dependents = [x.strip() for x in os.environ.get("DEPENDENTS", "").split(",") if x.strip()]

    check_interval = getenv_int("CHECK_INTERVAL", 10)
    startup_grace = getenv_int("STARTUP_GRACE", 90)
    down_grace = getenv_int("DOWN_GRACE", 180)
    cooldown = getenv_int("COOLDOWN", 120)

    verbose = getenv_bool("VERBOSE", True)
    print_health_logs = getenv_bool("PRINT_HEALTH_LOGS", True)
    log_tail = getenv_int("PRINT_CONTAINER_LOG_TAIL", 60)

    restart_vpn = getenv_bool("RESTART_VPN", False)

    # NUEVO: si el VPN se reinicia (restart count / StartedAt cambia), reinicia dependientes tras un pequeño grace
    restart_on_vpn_restart = getenv_bool("RESTART_ON_VPN_RESTART", True)
    vpn_restart_grace = getenv_int("VPN_RESTART_GRACE", 15)

    docker_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock").strip()
    docker_timeout = getenv_int("DOCKER_TIMEOUT", 5)

    api = DockerAPI(docker_host=docker_host, timeout=docker_timeout)

    log("========================================================")
    log("watchdog v3 (health + vpn-restart aware) - starting")
    log(f"DOCKER_HOST={docker_host} timeout={docker_timeout}s")
    log(f"VPN_CONTAINER={vpn_container}")
    log(f"DEPENDENTS={','.join(dependents) if dependents else '(none)'}")
    log(f"CHECK_INTERVAL={check_interval}s STARTUP_GRACE={startup_grace}s DOWN_GRACE={down_grace}s COOLDOWN={cooldown}s")
    log(f"RESTART_ON_VPN_RESTART={int(restart_on_vpn_restart)} VPN_RESTART_GRACE={vpn_restart_grace}s")
    log(f"RESTART_VPN={int(restart_vpn)} VERBOSE={int(verbose)} PRINT_HEALTH_LOGS={int(print_health_logs)} LOG_TAIL={log_tail}")
    log("========================================================")

    t0 = time.time()
    last_health_status = None
    last_state_status = None

    last_started_at = None
    last_restart_count = None
    pending_vpn_restart_since = None

    down_since = None
    next_action_after = 0.0

    while True:
        loop_t = time.time()
        uptime = int(loop_t - t0)

        try:
            insp = api.container_inspect(vpn_container)
            state = insp.get("State", {}) or {}
            state_status = state.get("Status")  # running/exited/...
            health = state.get("Health")
            health_summary = summarize_health(health)
            health_status = health_summary.get("Status") or "none"
            started_at = state.get("StartedAt")
            restart_count = int(state.get("RestartCount") or 0)
        except Exception as e:
            state_status = "missing"
            health_status = "missing"
            health_summary = {"error": str(e)[:300]}
            started_at = None
            restart_count = None
            down = True
        else:
            # Detect VPN container restart (even if it comes back healthy)
            if restart_on_vpn_restart:
                if last_started_at is not None and started_at and started_at != last_started_at:
                    pending_vpn_restart_since = loop_t
                    log(f"VPN restart detectado (StartedAt cambió): {last_started_at} -> {started_at}")
                if last_restart_count is not None and restart_count is not None and restart_count > last_restart_count:
                    pending_vpn_restart_since = loop_t
                    log(f"VPN restart detectado (RestartCount): {last_restart_count} -> {restart_count}")

            last_started_at = started_at
            last_restart_count = restart_count

            # Down definition: not running OR unhealthy
            if state_status != "running":
                down = True
            elif health_status == "unhealthy":
                down = True
            else:
                down = False

        # Print state change banners
        changed = (health_status != last_health_status) or (state_status != last_state_status)
        if changed:
            log(f"STATE CHANGE: docker_state={state_status} health={health_status}")
            if verbose:
                log("health_summary=" + json.dumps(health_summary, ensure_ascii=False))
            if print_health_logs and state_status == "running":
                try:
                    insp2 = api.container_inspect(vpn_container)
                    logs = ((insp2.get("State", {}) or {}).get("Health", {}) or {}).get("Log", []) or []
                    tail = logs[-5:]
                    for i, entry in enumerate(tail, 1):
                        out = (entry.get("Output") or "").strip().replace("\n", "\\n")
                        log(f"health_log[-{len(tail)-i+1}]: exit={entry.get('ExitCode')} start={entry.get('Start')} end={entry.get('End')} out='{out[:240]}'")
                except Exception as ex:
                    log(f"could not read health logs: {ex}")

        last_health_status = health_status
        last_state_status = state_status

        # Startup grace: ignore everything while just started
        if uptime < startup_grace:
            if verbose:
                log(f"startup grace: uptime={uptime}s (ignoring down={int(down)})")
            time.sleep(check_interval)
            continue

        # ACTION 1: VPN restarted -> restart dependents (after small grace)
        if pending_vpn_restart_since is not None:
            elapsed = int(loop_t - pending_vpn_restart_since)
            if elapsed >= vpn_restart_grace:
                if loop_t >= next_action_after:
                    targets = []
                    if restart_vpn:
                        targets.append(vpn_container)
                    targets.extend(dependents)
                    log(f"VPN se reinició; tras {elapsed}s => reiniciando dependientes para reenganchar network_mode:service")
                    restart_targets(api, targets, timeout_s=10, log_tail=log_tail, vpn_container=vpn_container)
                    next_action_after = time.time() + cooldown
                    pending_vpn_restart_since = None
                else:
                    if verbose:
                        log(f"VPN restart pendiente pero cooldown activo: {int(next_action_after-loop_t)}s")
            else:
                if verbose:
                    log(f"VPN restart pendiente: elapsed={elapsed}s (grace={vpn_restart_grace}s)")
            time.sleep(check_interval)
            continue

        # If not down: reset down timer
        if not down:
            if down_since is not None:
                log(f"RECOVERED: was down for {int(loop_t - down_since)}s; now healthy")
            down_since = None
            if verbose:
                log(f"OK: docker_state={state_status} health={health_status} uptime={uptime}s")
            time.sleep(check_interval)
            continue

        # It's down
        if down_since is None:
            down_since = loop_t
            log(f"caída detectada; grace {down_grace}s (docker_state={state_status}, health={health_status})")
            try:
                tail = api.container_logs_tail(vpn_container, tail=log_tail)
                log(f"--- {vpn_container} logs tail({log_tail}) ---\n{tail}\n--- end tail ---")
            except Exception as ex:
                log(f"could not fetch {vpn_container} logs: {ex}")

        elapsed = int(loop_t - down_since)
        log(f"sigue caído (elapsed={elapsed}s, docker_state={state_status}, health={health_status})")

        if elapsed < down_grace:
            time.sleep(check_interval)
            continue

        if loop_t < next_action_after:
            log(f"cooldown active: next_action_in={int(next_action_after - loop_t)}s")
            time.sleep(check_interval)
            continue

        targets = []
        if restart_vpn:
            targets.append(vpn_container)
        targets.extend(dependents)

        log(f">={down_grace}s => reiniciando por caída sostenida")
        restart_targets(api, targets, timeout_s=10, log_tail=log_tail, vpn_container=vpn_container)

        next_action_after = time.time() + cooldown
        down_since = time.time()
        time.sleep(check_interval)

    # unreachable
    # return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("stopped by user")
        sys.exit(0)
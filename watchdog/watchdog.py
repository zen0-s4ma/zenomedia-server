import os
import sys
import time
import json
import datetime as dt
from urllib.parse import quote

import requests_unixsocket


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


class DockerAPI:
    def __init__(self, sock_path: str = "/var/run/docker.sock", timeout: int = 5):
        self.sock_path = sock_path
        self.timeout = timeout
        self.session = requests_unixsocket.Session()
        # http+unix URL-encoded socket path
        self.base = f"http+unix://{quote(sock_path, safe='')}"
        # requests-unixsocket expects slashes encoded; quote() does it.

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
        # stdout+stderr, last N lines (best effort)
        url = self._url(f"/containers/{name}/logs?stdout=1&stderr=1&tail={tail}")
        r = self.session.get(url, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"GET logs {name} -> {r.status_code}: {r.text[:200]}")
        # Docker returns multiplexed stream sometimes; for simplicity print raw bytes decoded
        try:
            return r.content.decode("utf-8", errors="replace")
        except Exception:
            return str(r.content[:500])


def summarize_health(health: dict) -> dict:
    """
    Docker Health dict example:
    {
      "Status": "healthy|unhealthy|starting",
      "FailingStreak": 0,
      "Log": [{"Start":"...","End":"...","ExitCode":0,"Output":"..."}]
    }
    """
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

    docker_sock = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock").strip()

    api = DockerAPI(sock_path=docker_sock, timeout=5)

    log("========================================================")
    log("watchdog v2 (healthcheck-driven) - starting")
    log(f"VPN_CONTAINER={vpn_container}")
    log(f"DEPENDENTS={','.join(dependents) if dependents else '(none)'}")
    log(f"CHECK_INTERVAL={check_interval}s STARTUP_GRACE={startup_grace}s DOWN_GRACE={down_grace}s COOLDOWN={cooldown}s")
    log(f"DOCKER_SOCK={docker_sock}")
    log(f"RESTART_VPN={int(restart_vpn)} VERBOSE={int(verbose)} PRINT_HEALTH_LOGS={int(print_health_logs)} LOG_TAIL={log_tail}")
    log("========================================================")

    t0 = time.time()
    last_health_status = None
    last_state_status = None
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

        except Exception as e:
            # If we can't inspect, treat as down
            state_status = "missing"
            health_status = "missing"
            health_summary = {"error": str(e)[:300]}
            down = True
        else:
            # Down definition: container not running OR health says unhealthy
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
                # Print last few health log entries (if available)
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

        # Startup grace: ignore "down" while just started
        if uptime < startup_grace:
            if verbose:
                log(f"startup grace: uptime={uptime}s (ignoring down={int(down)})")
            time.sleep(check_interval)
            continue

        if not down:
            # reset down timer
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

            # Print Gluetun logs immediately on first detection
            try:
                tail = api.container_logs_tail(vpn_container, tail=log_tail)
                log(f"--- {vpn_container} logs tail({log_tail}) ---\n{tail}\n--- end tail ---")
            except Exception as ex:
                log(f"could not fetch {vpn_container} logs: {ex}")

        elapsed = int(loop_t - down_since)
        log(f"sigue caído (elapsed={elapsed}s, docker_state={state_status}, health={health_status})")

        # If still within grace, wait
        if elapsed < down_grace:
            time.sleep(check_interval)
            continue

        # Cooldown to avoid flapping loops
        if loop_t < next_action_after:
            log(f"cooldown active: next_action_in={int(next_action_after - loop_t)}s")
            time.sleep(check_interval)
            continue

        # ACTION: restart dependents (and optionally VPN container)
        targets = []
        if restart_vpn:
            targets.append(vpn_container)
        targets.extend(dependents)

        log(f">={down_grace}s => reiniciando: {','.join(targets) if targets else '(none)'}")
        for name in targets:
            if not name:
                continue
            try:
                api.container_restart(name, timeout_s=10)
                log(f"restart OK: {name}")
            except Exception as e:
                # if restart fails because container stopped/missing, try start
                msg = str(e)
                log(f"restart FAIL: {name}: {msg[:200]}")
                try:
                    api.container_start(name)
                    log(f"start OK (fallback): {name}")
                except Exception as e2:
                    log(f"start FAIL: {name}: {str(e2)[:200]}")

        # After taking action, wait for cooldown
        next_action_after = time.time() + cooldown
        down_since = time.time()  # reset timer after action
        time.sleep(check_interval)

    # unreachable
    # return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("stopped by user")
        sys.exit(0)
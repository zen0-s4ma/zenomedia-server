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


# -------------------------------
# Utils
# -------------------------------

def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"{now_str()} | [watchdog] {msg}", flush=True)


def getenv_str(name: str, default: str = "") -> str:
    v = os.environ.get(name, "")
    v = v.strip() if isinstance(v, str) else ""
    return v or default


def getenv_int(name: str, default: int) -> int:
    v = getenv_str(name, "")
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def getenv_bool(name: str, default: bool = False) -> bool:
    v = getenv_str(name, "").lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


def normalize_docker_host(raw: str) -> tuple[str, str]:
    """Return (mode, base_url). mode is 'unix' or 'http'."""
    raw = (raw or "").strip()
    if not raw:
        raw = "unix:///var/run/docker.sock"

    if raw.startswith("unix://"):
        sock_path = raw[len("unix://") :]
        return "unix", f"http+unix://{quote(sock_path, safe='')}"

    if raw.startswith("tcp://"):
        raw = "http://" + raw[len("tcp://") :]

    if raw.startswith("http://") or raw.startswith("https://"):
        return "http", raw.rstrip("/")

    return "http", ("http://" + raw).rstrip("/")


def _brief(s: str, n: int = 240) -> str:
    s = (s or "").replace("\n", " ")
    return s[:n]


def is_netns_join_error(msg: str) -> bool:
    msg = (msg or "").lower()
    return ("joining network namespace" in msg) and ("no such container" in msg)


# -------------------------------
# Docker API
# -------------------------------

class DockerAPI:
    def __init__(self, docker_host: str, timeout: int = 10, retries: int = 3, retry_sleep: float = 1.0):
        self.mode, self.base = normalize_docker_host(docker_host)
        self.timeout = timeout
        self.retries = max(1, retries)
        self.retry_sleep = max(0.2, float(retry_sleep))

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

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self._url(path)
        last_exc = None

        for attempt in range(1, self.retries + 1):
            try:
                r = self.session.request(method, url, timeout=self.timeout, **kwargs)

                # Retry on transient 5xx
                if r.status_code >= 500 and attempt < self.retries:
                    time.sleep(self.retry_sleep * attempt)
                    continue

                return r
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_exc = e
                if attempt < self.retries:
                    time.sleep(self.retry_sleep * attempt)
                    continue
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError("unexpected request error")

    def get_json(self, path: str):
        r = self.request("GET", path)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} -> {r.status_code}: {_brief(r.text, 300)}")
        return r.json()

    def post(self, path: str, **kwargs):
        r = self.request("POST", path, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {r.status_code}: {_brief(r.text, 300)}")
        return r

    def delete(self, path: str, **kwargs):
        r = self.request("DELETE", path, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"DELETE {path} -> {r.status_code}: {_brief(r.text, 300)}")
        return r

    # Containers
    def container_inspect(self, name_or_id: str):
        return self.get_json(f"/containers/{name_or_id}/json")

    def container_restart(self, name_or_id: str, timeout_s: int = 30):
        return self.post(f"/containers/{name_or_id}/restart?t={int(timeout_s)}")

    def container_start(self, name_or_id: str):
        return self.post(f"/containers/{name_or_id}/start")

    def container_rename(self, name_or_id: str, new_name: str):
        # Docker API requires URL-encoded name
        return self.post(f"/containers/{name_or_id}/rename?name={quote(new_name)}")

    def container_stop(self, name_or_id: str, timeout_s: int = 20):
        return self.post(f"/containers/{name_or_id}/stop?t={int(timeout_s)}")

    def container_remove(self, name_or_id: str, force: bool = True, volumes: bool = False):
        qs = f"force={'true' if force else 'false'}&v={'true' if volumes else 'false'}"
        return self.delete(f"/containers/{name_or_id}?{qs}")

    def container_create(self, name: str, payload: dict):
        # name must be query string
        return self.post(f"/containers/create?name={quote(name)}", json=payload)

    def container_logs_tail(self, name_or_id: str, tail: int = 60):
        url = f"/containers/{name_or_id}/logs?stdout=1&stderr=1&tail={int(tail)}"
        r = self.request("GET", url)
        if r.status_code >= 400:
            raise RuntimeError(f"GET logs {name_or_id} -> {r.status_code}: {_brief(r.text, 200)}")
        try:
            return r.content.decode("utf-8", errors="replace")
        except Exception:
            return str(r.content[:500])


# -------------------------------
# Recreate logic
# -------------------------------

_ALLOWED_HOSTCONFIG_KEYS = {
    "Binds",
    "Mounts",
    "RestartPolicy",
    "NetworkMode",
    "ShmSize",
    "CapAdd",
    "CapDrop",
    "Devices",
    "Sysctls",
    "Privileged",
    "ReadOnlyRootfs",
    "SecurityOpt",
    "LogConfig",
    "Dns",
    "DnsOptions",
    "DnsSearch",
    "ExtraHosts",
    "Ulimits",
    "OomKillDisable",
    "Init",
}


def build_create_payload_from_inspect(dep_insp: dict, vpn_id: str) -> dict:
    cfg = (dep_insp.get("Config") or {})
    hostcfg = (dep_insp.get("HostConfig") or {})

    # Sanitize HostConfig
    new_hostcfg = {}
    for k in _ALLOWED_HOSTCONFIG_KEYS:
        if k in hostcfg and hostcfg[k] is not None:
            new_hostcfg[k] = hostcfg[k]

    # Force correct network namespace
    new_hostcfg["NetworkMode"] = f"container:{vpn_id}"

    payload: dict = {
        "Image": cfg.get("Image"),
        "Env": cfg.get("Env"),
        "Cmd": cfg.get("Cmd"),
        "Entrypoint": cfg.get("Entrypoint"),
        "WorkingDir": cfg.get("WorkingDir"),
        "User": cfg.get("User"),
        "Labels": cfg.get("Labels"),
        "Healthcheck": cfg.get("Healthcheck"),
        "HostConfig": new_hostcfg,
    }

    # Remove nulls (Docker is picky with some fields)
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    return payload


def recreate_container(api: DockerAPI, name: str, old_inspect: dict, new_netns_id: str, log) -> bool:
    """
    Recreate a container so it joins the VPN container network namespace.

    Important: When using NetworkMode=container:<id>, Docker forbids port publishing/exposing options.
    We therefore rebuild using a filtered create payload and also perform a *safe* replace:
    - stop old
    - rename old -> backup
    - create+start new with original name
    - remove old backup
    If create fails, we attempt to restore the old name and start it again.
    """
    if not old_inspect:
        log(f"recreate: no inspect template for {name}; cannot recreate (run `docker compose up -d {name}` once)")
        return False

    backup_name = f"{name}-wd-old-{int(time.time())}"
    old_id = None
    if old_inspect:
        old_id = old_inspect.get("Id") or None

    try:
        if old_id:
            # stop first to avoid two writers on same volumes
            try:
                api.container_stop(name, timeout_s=int(os.environ.get("STOP_TIMEOUT_S", "20")))
            except Exception as e:
                # If it's already stopped/dead, continue
                log(f"recreate: stop {name} WARN: {e}")
            # rename old away so we can create the new container with the original name
            try:
                api.container_rename(old_id, backup_name)
            except Exception as e:
                log(f"recreate: rename old {name} -> {backup_name} FAIL: {e}")
                return False

        # Create the new container with the original name
        payload = build_create_payload_from_inspect(old_inspect, new_netns_id)
        created = api.container_create(name, payload)
        new_id = created.get("Id") if isinstance(created, dict) else None
        if not new_id:
            raise DockerAPIException("container_create returned no Id")

        api.container_start(new_id)

        # Remove old backup if any
        if old_id:
            try:
                api.container_remove(backup_name, force=True)
            except Exception as e:
                log(f"recreate: remove old backup {backup_name} WARN: {e}")

        return True

    except Exception as e:
        # Rollback: try to restore old container name and start it again
        log(f"recreate: create/start {name} FAIL: {e}")
        if old_id:
            try:
                # If new container with 'name' exists partially, best effort remove it
                try:
                    api.container_remove(name, force=True)
                except Exception:
                    pass
                api.container_rename(backup_name, name)
                try:
                    api.container_start(name)
                except Exception:
                    pass
            except Exception as e2:
                log(f"recreate: rollback FAIL: {e2}")
        return False

def get_container_id(insp: dict) -> str | None:
    cid = insp.get("Id")
    if isinstance(cid, str) and cid:
        return cid
    return None


def get_network_mode(insp: dict) -> str:
    return ((insp.get("HostConfig") or {}).get("NetworkMode") or "")


def extract_container_target_id(network_mode: str) -> str | None:
    # network_mode looks like: "container:<id>"
    if not network_mode:
        return None
    if network_mode.startswith("container:"):
        return network_mode.split(":", 1)[1]
    return None


def ensure_dependents_attached(api: DockerAPI, vpn_id: str, dependents: list[str], stop_timeout_s: int, enabled: bool, verbose: bool) -> int:
    """Return number of recreated containers."""
    recreated = 0
    if not enabled:
        return 0

    for name in dependents:
        try:
            dinsp = api.container_inspect(name)
        except Exception as e:
            if verbose:
                log(f"netns-check: cannot inspect {name}: {_brief(str(e), 220)}")
            continue

        nm = get_network_mode(dinsp)
        tgt = extract_container_target_id(nm)

        # Only fix those that are in container network mode.
        if not tgt:
            continue

        if tgt != vpn_id:
            log(f"netns mismatch: {name} has {tgt[:12]} but vpn is {vpn_id[:12]} -> recreate")
            ok = recreate_container(api, name=name, vpn_id=vpn_id, stop_timeout_s=stop_timeout_s, verbose=verbose)
            if ok:
                recreated += 1

    return recreated


# -------------------------------
# Health summary / actions
# -------------------------------


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


def restart_or_recreate_targets(
    api: DockerAPI,
    vpn_id: str,
    vpn_container: str,
    targets: list[str],
    restart_timeout_s: int,
    stop_timeout_s: int,
    recreate_on_netns_error: bool,
    log_tail: int,
):
    log(f"accion sobre: {','.join(targets) if targets else '(none)'}")

    for name in targets:
        if not name:
            continue

        try:
            api.container_restart(name, timeout_s=restart_timeout_s)
            log(f"restart OK: {name}")
            continue
        except Exception as e:
            msg = str(e)
            log(f"restart FAIL: {name}: {_brief(msg, 220)}")

            # Netns missing -> recreate is the only real fix
            if recreate_on_netns_error and is_netns_join_error(msg):
                log(f"netns error detected on {name} -> recreate")
                recreate_container(api, name=name, vpn_id=vpn_id, stop_timeout_s=stop_timeout_s)
                continue

            # Fallback: try start
            try:
                api.container_start(name)
                log(f"start OK (fallback): {name}")
            except Exception as e2:
                log(f"start FAIL: {name}: {_brief(str(e2), 220)}")
                if recreate_on_netns_error and is_netns_join_error(str(e2)):
                    log(f"netns error detected on start {name} -> recreate")
                    recreate_container(api, name=name, vpn_id=vpn_id, stop_timeout_s=stop_timeout_s)

    # tail logs del VPN para diagnóstico
    try:
        tail = api.container_logs_tail(vpn_container, tail=log_tail)
        log(f"--- {vpn_container} logs tail({log_tail}) ---\n{tail}\n--- end tail ---")
    except Exception as ex:
        log(f"could not fetch {vpn_container} logs: {ex}")


# -------------------------------
# Main
# -------------------------------


def main() -> int:
    vpn_container = getenv_str("VPN_CONTAINER", "vpn-stable")
    dependents = [x.strip() for x in getenv_str("DEPENDENTS", "").split(",") if x.strip()]

    check_interval = getenv_int("CHECK_INTERVAL", 10)
    startup_grace = getenv_int("STARTUP_GRACE", 90)
    down_grace = getenv_int("DOWN_GRACE", 180)
    cooldown = getenv_int("COOLDOWN", 120)

    verbose = getenv_bool("VERBOSE", True)
    print_health_logs = getenv_bool("PRINT_HEALTH_LOGS", True)
    log_tail = getenv_int("PRINT_CONTAINER_LOG_TAIL", 60)

    restart_vpn = getenv_bool("RESTART_VPN", False)

    restart_on_vpn_restart = getenv_bool("RESTART_ON_VPN_RESTART", True)
    vpn_restart_grace = getenv_int("VPN_RESTART_GRACE", 15)

    # v4
    recreate_on_netns_mismatch = getenv_bool("RECREATE_ON_NETNS_MISMATCH", True)
    recreate_on_netns_error = getenv_bool("RECREATE_ON_NETNS_ERROR", True)
    stop_timeout_s = getenv_int("STOP_TIMEOUT_S", 20)
    restart_timeout_s = getenv_int("RESTART_TIMEOUT_S", 30)

    docker_host = getenv_str("DOCKER_HOST", "unix:///var/run/docker.sock")
    docker_timeout = getenv_int("DOCKER_TIMEOUT", 30)
    api_retries = getenv_int("DOCKER_RETRIES", 3)
    api_retry_sleep = float(getenv_str("DOCKER_RETRY_SLEEP", "1"))

    api = DockerAPI(docker_host=docker_host, timeout=docker_timeout, retries=api_retries, retry_sleep=api_retry_sleep)

    log("========================================================")
    log("watchdog v4 (restart + netns-recreate) - starting")
    log(f"DOCKER_HOST={docker_host} timeout={docker_timeout}s retries={api_retries}")
    log(f"VPN_CONTAINER={vpn_container}")
    log(f"DEPENDENTS={','.join(dependents) if dependents else '(none)'}")
    log(f"CHECK_INTERVAL={check_interval}s STARTUP_GRACE={startup_grace}s DOWN_GRACE={down_grace}s COOLDOWN={cooldown}s")
    log(f"RESTART_ON_VPN_RESTART={int(restart_on_vpn_restart)} VPN_RESTART_GRACE={vpn_restart_grace}s")
    log(f"RECREATE_ON_NETNS_MISMATCH={int(recreate_on_netns_mismatch)} RECREATE_ON_NETNS_ERROR={int(recreate_on_netns_error)}")
    log(f"STOP_TIMEOUT_S={stop_timeout_s} RESTART_TIMEOUT_S={restart_timeout_s}")
    log(f"RESTART_VPN={int(restart_vpn)} VERBOSE={int(verbose)} PRINT_HEALTH_LOGS={int(print_health_logs)} LOG_TAIL={log_tail}")
    log("========================================================")

    t0 = time.time()

    last_health_status = None
    last_state_status = None

    last_vpn_id: str | None = None
    last_started_at: str | None = None
    last_restart_count: int | None = None

    pending_vpn_restart_since: float | None = None
    pending_vpn_id_change: bool = False

    down_since: float | None = None
    next_action_after: float = 0.0

    while True:
        loop_t = time.time()
        uptime = int(loop_t - t0)

        vpn_id = None
        state_status = "missing"
        health_status = "missing"
        health_summary = {}
        started_at = None
        restart_count = None
        down = True

        try:
            insp = api.container_inspect(vpn_container)
            vpn_id = get_container_id(insp)
            state = (insp.get("State") or {})
            state_status = state.get("Status")  # running/exited/...
            health = state.get("Health")
            health_summary = summarize_health(health)
            health_status = health_summary.get("Status") or "none"
            started_at = state.get("StartedAt")
            restart_count = int(state.get("RestartCount") or 0)

            # Down definition
            if state_status != "running":
                down = True
            elif health_status == "unhealthy":
                down = True
            else:
                down = False

        except Exception as e:
            health_summary = {"error": _brief(str(e), 300)}

        # Detect vpn restarts / id changes
        if vpn_id:
            if last_vpn_id is not None and vpn_id != last_vpn_id:
                pending_vpn_restart_since = loop_t
                pending_vpn_id_change = True
                log(f"VPN ID cambió (recreate probable): {last_vpn_id[:12]} -> {vpn_id[:12]}")

            if restart_on_vpn_restart:
                if last_started_at is not None and started_at and started_at != last_started_at:
                    pending_vpn_restart_since = loop_t
                    log(f"VPN restart detectado (StartedAt cambió): {last_started_at} -> {started_at}")
                if last_restart_count is not None and restart_count is not None and restart_count > last_restart_count:
                    pending_vpn_restart_since = loop_t
                    log(f"VPN restart detectado (RestartCount): {last_restart_count} -> {restart_count}")

            last_vpn_id = vpn_id
            last_started_at = started_at
            last_restart_count = restart_count

        # Print state change
        changed = (health_status != last_health_status) or (state_status != last_state_status)
        if changed:
            log(f"STATE CHANGE: docker_state={state_status} health={health_status}")
            if verbose:
                log("health_summary=" + json.dumps(health_summary, ensure_ascii=False))
            if print_health_logs and state_status == "running":
                try:
                    insp2 = api.container_inspect(vpn_container)
                    logs = (((insp2.get("State") or {}).get("Health") or {}).get("Log") or [])
                    tail = logs[-5:]
                    for i, entry in enumerate(tail, 1):
                        out = (entry.get("Output") or "").strip().replace("\n", "\\n")
                        log(
                            f"health_log[-{len(tail)-i+1}]: exit={entry.get('ExitCode')} start={entry.get('Start')} end={entry.get('End')} out='{out[:240]}'"
                        )
                except Exception as ex:
                    log(f"could not read health logs: {ex}")

        last_health_status = health_status
        last_state_status = state_status

        # Startup grace
        if uptime < startup_grace:
            if verbose:
                log(f"startup grace: uptime={uptime}s (ignoring down={int(down)})")
            time.sleep(check_interval)
            continue

        # Continuous netns mismatch guard (fix even if VPN looks OK)
        if vpn_id and dependents:
            ensure_dependents_attached(
                api,
                vpn_id=vpn_id,
                dependents=dependents,
                stop_timeout_s=stop_timeout_s,
                enabled=recreate_on_netns_mismatch,
                verbose=verbose,
            )

        # ACTION: VPN restart detected
        if pending_vpn_restart_since is not None and vpn_id:
            elapsed = int(loop_t - pending_vpn_restart_since)
            if elapsed >= vpn_restart_grace:
                if loop_t >= next_action_after:
                    # If VPN id changed, we MUST recreate dependents.
                    if pending_vpn_id_change and recreate_on_netns_mismatch:
                        log(f"VPN reiniciado + ID cambió; tras {elapsed}s => recreando dependientes")
                        ensure_dependents_attached(
                            api,
                            vpn_id=vpn_id,
                            dependents=dependents,
                            stop_timeout_s=stop_timeout_s,
                            enabled=True,
                            verbose=verbose,
                        )
                    else:
                        targets = []
                        if restart_vpn:
                            targets.append(vpn_container)
                        targets.extend(dependents)
                        log(f"VPN se reinició; tras {elapsed}s => reiniciando dependientes")
                        restart_or_recreate_targets(
                            api,
                            vpn_id=vpn_id,
                            vpn_container=vpn_container,
                            targets=targets,
                            restart_timeout_s=restart_timeout_s,
                            stop_timeout_s=stop_timeout_s,
                            recreate_on_netns_error=recreate_on_netns_error,
                            log_tail=log_tail,
                        )

                    next_action_after = time.time() + cooldown
                    pending_vpn_restart_since = None
                    pending_vpn_id_change = False
                else:
                    if verbose:
                        log(f"VPN restart pendiente pero cooldown activo: {int(next_action_after-loop_t)}s")
            else:
                if verbose:
                    log(f"VPN restart pendiente: elapsed={elapsed}s (grace={vpn_restart_grace}s)")

            time.sleep(check_interval)
            continue

        # If not down
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

        if not vpn_id:
            log("VPN container no visible (sin ID) -> esperando")
            time.sleep(check_interval)
            continue

        targets = []
        if restart_vpn:
            targets.append(vpn_container)
        targets.extend(dependents)

        log(f">={down_grace}s => reiniciando por caída sostenida")
        restart_or_recreate_targets(
            api,
            vpn_id=vpn_id,
            vpn_container=vpn_container,
            targets=targets,
            restart_timeout_s=restart_timeout_s,
            stop_timeout_s=stop_timeout_s,
            recreate_on_netns_error=recreate_on_netns_error,
            log_tail=log_tail,
        )

        next_action_after = time.time() + cooldown
        down_since = time.time()
        time.sleep(check_interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("stopped by user")
        sys.exit(0)

"""IKEv2 (Lin Song/hwdsl2 + Libreswan) provider for tunnel_api.

Only certificate identity and lease= are exported. Outer peer endpoints and raw
command output are never included in peer_list or detail data.
"""
import ipaddress
import os
import re
import shutil
import subprocess
import time

DEFAULT_CONFIG = {
    "IKEV2_DOCKER": True,
    "IKEV2_DOCKER_CLI": "docker",
    "IKEV2_CONTAINER": "ipsec-vpn-server",
    "IKEV2_IPSEC_CLI": "ipsec",
    "IKEV2_SCRIPT": "ikev2.sh",
    "IKEV2_CONNECTION_PREFIX": "ikev2-cp",
    "IKEV2_TIMEOUT": 8,
    "IKEV2_CLIENT_CACHE_SECONDS": 60,
}

# Deliberately use configure(), not CONFIG_FIELDS: the existing installer calls
# this hook for conditional Docker/native questions without UI changes.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}\Z")
_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HEADER = re.compile(r'^\s*(?:\d{3}\s+)?#\d+:\s+"([^"\r\n]+)"(?:\[\d+\])?(?:\s|,|$)')
_ID = re.compile(r"\bid\s*=\s*(?:'((?:\\.|[^'\\])*)'|\"((?:\\.|[^\"\\])*)\")")
_LEASE = re.compile(r"\blease\s*=\s*([^,\s]+)")
_TOTAL = re.compile(r"Total:\s*(\d+)\s+clients?(?:\(s\))?\.?", re.I)
_catalog_cache = None


def _command_text(value):
    return (isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and not value.startswith("-") and not any(ord(c) < 32 for c in value))


def validate_config(config):
    if type(config.get("IKEV2_DOCKER")) is not bool:
        raise ValueError("IKEV2_DOCKER 必须为布尔值")
    for key in ("IKEV2_IPSEC_CLI", "IKEV2_SCRIPT"):
        if not _command_text(config.get(key)):
            raise ValueError(key + " 必须为有效命令或路径")
    if config["IKEV2_DOCKER"]:
        if not _command_text(config.get("IKEV2_DOCKER_CLI")):
            raise ValueError("IKEV2_DOCKER_CLI 无效")
        container = config.get("IKEV2_CONTAINER")
        if not isinstance(container, str) or not _CONTAINER.fullmatch(container):
            raise ValueError("IKEV2_CONTAINER 必须为容器名称或ID")
    prefix = config.get("IKEV2_CONNECTION_PREFIX")
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", prefix):
        raise ValueError("IKEV2_CONNECTION_PREFIX 无效")
    for key, low, high in (("IKEV2_TIMEOUT", 1, 60), ("IKEV2_CLIENT_CACHE_SECONDS", 0, 3600)):
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} 必须为 {low}~{high} 的整数")


def configure(old, ask, integer, executable, tty):
    """Conditional prompts; return ALL defaults to avoid generic extra questions."""
    config = {key: old.get(key, value) for key, value in DEFAULT_CONFIG.items()}
    default = "y" if config["IKEV2_DOCKER"] else "n"
    while True:
        selected = str(ask("IKEv2 是否安装在 Docker 中？y/n", default)).strip().lower()
        if selected in ("y", "yes", "true", "1", "n", "no", "false", "0"):
            config["IKEV2_DOCKER"] = selected in ("y", "yes", "true", "1")
            break
        tty.write("请输入 y 或 n。\n")
    if config["IKEV2_DOCKER"]:
        path = config["IKEV2_DOCKER_CLI"]
        path = shutil.which(path) or (path if os.path.isabs(path) else "/usr/bin/docker")
        if old.get("IKEV2_DOCKER") is False:
            # Native host paths are not assumed to exist inside a container.
            config["IKEV2_IPSEC_CLI"] = DEFAULT_CONFIG["IKEV2_IPSEC_CLI"]
            config["IKEV2_SCRIPT"] = DEFAULT_CONFIG["IKEV2_SCRIPT"]
        config["IKEV2_DOCKER_CLI"] = executable("Docker 路径", path, "请填写 Docker 可执行文件的绝对路径。")
        while True:
            name = ask("IKEv2 容器名", config["IKEV2_CONTAINER"])
            if isinstance(name, str) and _CONTAINER.fullmatch(name):
                config["IKEV2_CONTAINER"] = name
                break
            tty.write("容器名称无效。\n")
    else:
        for key, label, fallback in (
            ("IKEV2_IPSEC_CLI", "ipsec 路径", "/usr/sbin/ipsec"),
            ("IKEV2_SCRIPT", "ikev2.sh 路径", "/usr/bin/ikev2.sh"),
        ):
            path = shutil.which(config[key]) or (config[key] if os.path.isabs(config[key]) else fallback)
            config[key] = executable(label, path, "请填写存在且可执行的绝对路径。")
    config["IKEV2_TIMEOUT"] = integer("IKEv2 命令超时（秒）", config["IKEV2_TIMEOUT"], 1, 60)
    validate_config(config)
    return config


def _run(config, program, *args):
    command = [program, *args]
    if config["IKEV2_DOCKER"]:
        command = [config["IKEV2_DOCKER_CLI"], "exec", "-e", "LC_ALL=C", "-e", "LANG=C",
                   config["IKEV2_CONTAINER"], *command]
    env = dict(os.environ, LC_ALL="C", LANG="C")
    response = subprocess.run(command, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=config["IKEV2_TIMEOUT"], env=env)
    if response.returncode != 0:
        # Do not propagate raw stderr: it may contain endpoint addresses.
        raise ValueError("IKEv2 command failed")
    if not isinstance(response.stdout, str) or len(response.stdout) > 4 * 1024 * 1024:
        raise ValueError("invalid IKEv2 output")
    return _ANSI.sub("", response.stdout)


def parse_clients(text):
    """Parse the documented English client table; unknown format is a failure."""
    if not isinstance(text, str):
        raise ValueError("client table must be text")
    lines = [_ANSI.sub("", line).strip() for line in text.splitlines()]
    headers = [i for i, line in enumerate(lines)
               if re.fullmatch(r"Client\s+Name\s+Certificate\s+Status", line, re.I)]
    if len(headers) != 1:
        raise ValueError("client table header missing")
    clients, total, separator = {}, None, False
    for line in lines[headers[0] + 1:]:
        if not line:
            continue
        if re.fullmatch(r"-+\s+-+", line):
            if clients or separator:
                raise ValueError("unexpected table separator")
            separator = True
            continue
        count = _TOTAL.fullmatch(line)
        if count:
            if total is not None:
                raise ValueError("duplicate client count")
            total = int(count.group(1))
            continue
        if total is not None:
            raise ValueError("unexpected data after client count")
        row = re.fullmatch(r"(\S+)\s+(valid|revoked|expired)(?:\s+\([^\r\n]*\))?", line, re.I)
        if not separator or not row or not _NAME.fullmatch(row.group(1)):
            raise ValueError("invalid client row")
        name, status = row.group(1), row.group(2).lower()
        if name in clients:
            raise ValueError("duplicate client name")
        clients[name] = status
    if total is None or total != len(clients):
        raise ValueError("client count mismatch")
    return clients


def _identity_name(identity):
    if identity.startswith("@"):
        name = identity[1:]
    elif re.search(r"(?:^|,)\s*CN\s*=", identity, re.I):
        matches = re.findall(r"(?:^|(?<!\\),)\s*CN\s*=\s*((?:\\.|[^,])*)", identity, re.I)
        if len(matches) != 1:
            raise ValueError("ambiguous certificate CN")
        # Decode basic DN escapes; names containing separators are rejected below.
        name = re.sub(r"\\([0-9A-Fa-f]{2}|.)", lambda m: chr(int(m.group(1), 16))
                      if re.fullmatch(r"[0-9A-Fa-f]{2}", m.group(1)) else m.group(1), matches[0]).strip()
    else:
        name = identity
        try:
            ipaddress.ip_address(name)
        except ValueError:
            pass
        else:
            raise ValueError("address-only IKE identity is unsupported")
    if not _NAME.fullmatch(name):
        raise ValueError("unsupported IKEv2 identity")
    return name


def parse_trafficstatus(text, connection_prefix="ikev2-cp"):
    """Return {client_name: set(lease_addresses)}; never scan outer addresses."""
    if not isinstance(text, str):
        raise ValueError("trafficstatus must be text")
    active = {}
    for raw_line in text.splitlines():
        line = _ANSI.sub("", raw_line).strip()
        if not line:
            continue
        header = _HEADER.match(line)
        if not header:
            raise ValueError("unrecognized trafficstatus row")
        connection = header.group(1)
        if connection != connection_prefix and not connection.startswith(connection_prefix + "-"):
            continue
        body = line[header.end():]
        if not re.search(r"\btype\s*=\s*ESP\b", body):
            raise ValueError("expected established ESP state")
        ids = list(_ID.finditer(body))
        if len(ids) != 1:
            raise ValueError("missing or ambiguous IKEv2 identity")
        name = _identity_name(ids[0].group(1) if ids[0].group(1) is not None else ids[0].group(2))
        leases = list(_LEASE.finditer(body))
        if len(leases) > 1:
            raise ValueError("ambiguous lease")
        addresses = active.setdefault(name, set())
        if leases:
            value = leases[0].group(1)
            interface = ipaddress.ip_interface(value)
            if interface.network.prefixlen != interface.max_prefixlen:
                raise ValueError("lease must be a single client address")
            addresses.add(str(interface.ip))
        elif re.search(r"\blease\s*=", body):
            raise ValueError("malformed lease")
        # Without lease, hostname is still valid; ipv4 stays empty.
    return active


def _catalog(config, force=False):
    global _catalog_cache
    key = tuple(config[k] for k in ("IKEV2_DOCKER", "IKEV2_DOCKER_CLI", "IKEV2_CONTAINER", "IKEV2_SCRIPT"))
    now = time.monotonic()
    ttl = config["IKEV2_CLIENT_CACHE_SECONDS"]
    if not force and _catalog_cache is not None:
        cached_key, cached_at, clients = _catalog_cache
        if cached_key == key and 0 <= now - cached_at < ttl:
            return dict(clients), True
    clients = parse_clients(_run(config, config["IKEV2_SCRIPT"], "--listclients"))
    _catalog_cache = (key, time.monotonic(), dict(clients))
    return clients, False


def collect(config):
    """Known clients become offline rows when no matching ESP state exists."""
    try:
        merged = dict(DEFAULT_CONFIG)
        merged.update(config)
        validate_config(merged)
        clients, cached = _catalog(merged)
        active = parse_trafficstatus(_run(merged, merged["IKEV2_IPSEC_CLI"], "trafficstatus"),
                                     merged["IKEV2_CONNECTION_PREFIX"])
        if cached and any(name not in clients for name in active):
            clients, _ = _catalog(merged, force=True)
        names = sorted(set(clients) | set(active))
        if not names:
            return None  # Core requires a non-empty peer list.
        peers, details = [], []
        for name in names:
            addresses = sorted(active.get(name, set()))
            # A profile may have multiple SAs/leases: one stable hostname row.
            # Export an IP only if there is exactly one unambiguous IPv4 lease.
            ipv4 = addresses[0] if len(addresses) == 1 and ipaddress.ip_address(addresses[0]).version == 4 else ""
            peers.append({"ipv4": ipv4, "hostname": name, "is_online": name in active})
            details.append({"hostname": name, "certificate_status": clients.get(name, "unlisted"),
                            "is_online": name in active, "lease_addresses": addresses})
        return peers, {"clients": details}
    except Exception:
        return None


def detail(raw, config):
    """Only sanitized identity/lease data; never raw command output."""
    if not isinstance(raw, dict) or not isinstance(raw.get("clients"), list):
        return False, None
    allowed = ("hostname", "certificate_status", "is_online", "lease_addresses")
    return True, {"clients": [{key: row.get(key) for key in allowed} for row in raw["clients"]]}

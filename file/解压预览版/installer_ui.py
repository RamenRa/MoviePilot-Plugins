"""Standard-library-only installer input, declarative Provider forms and summary."""
import atexit
import codecs
import json
import os
import shutil
from pathlib import Path
import termios
import unicodedata


def _display_width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


class TerminalUI:
    def __init__(self, device="/dev/tty"):
        self.fd = os.open(device, os.O_RDWR | getattr(os, "O_NOCTTY", 0))
        atexit.register(self.close)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def write(self, text):
        data = str(text).encode("utf-8")
        while data:
            count = os.write(self.fd, data)
            data = data[count:]

    def _line(self, prompt, secret=False):
        saved = termios.tcgetattr(self.fd)
        settings = list(saved)
        settings[6] = list(saved[6])
        settings[3] &= ~(termios.ICANON | termios.ECHO | getattr(termios, "ECHONL", 0))
        settings[0] |= termios.ICRNL
        settings[0] &= ~(getattr(termios, "IGNCR", 0) | getattr(termios, "INLCR", 0))
        settings[6][termios.VMIN] = 1
        settings[6][termios.VTIME] = 0
        buffer = []
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        escape = 0

        def erase():
            if buffer:
                char = buffer.pop()
                if not secret:
                    self.write("\b \b" * _display_width(char))

        try:
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
            self.write(prompt)
            while True:
                byte = os.read(self.fd, 1)
                if not byte:
                    raise EOFError("终端输入已关闭")
                if escape:
                    if escape == 1:
                        escape = 2 if byte in (b"[", b"O") else 0
                    elif 0x40 <= byte[0] <= 0x7e:
                        escape = 0
                    continue
                if byte == b"\x1b":
                    escape = 1
                    continue
                if byte in (b"\n", b"\r"):
                    self.write("\n")
                    return "".join(buffer)
                if byte in (b"\x08", b"\x7f"):
                    decoder.reset()
                    erase()
                    continue
                if byte == b"\x15":  # Ctrl-U
                    decoder.reset()
                    while buffer:
                        erase()
                    continue
                if byte == b"\x17":  # Ctrl-W
                    decoder.reset()
                    while buffer and buffer[-1].isspace():
                        erase()
                    while buffer and not buffer[-1].isspace():
                        erase()
                    continue
                if byte == b"\x03":
                    raise KeyboardInterrupt
                if byte == b"\x04":
                    raise EOFError("用户取消输入")
                if byte[0] < 32:
                    continue
                chars = decoder.decode(byte)
                for char in chars:
                    if char.isprintable():
                        buffer.append(char)
                        if not secret:
                            self.write(char)
        finally:
            termios.tcsetattr(self.fd, termios.TCSANOW, saved)


    def _width(self):
        try:
            return max(40, min(100, os.get_terminal_size(self.fd).columns))
        except (OSError, AttributeError):
            return 80

    def _wrapped(self, text, prefix="  ", continuation="    "):
        width = self._width() - 1
        line, used = prefix, sum(_display_width(c) for c in prefix)
        for char in str(text):
            if not char.isprintable():
                char = " "
            size = _display_width(char)
            if used + size > width:
                self.write(line.rstrip() + "\n")
                line, used = continuation, sum(_display_width(c) for c in continuation)
            line += char
            used += size
        self.write(line.rstrip() + "\n")

    def section(self, title):
        title = "".join(c for c in str(title) if c.isprintable())
        self.write("\n【" + title + "】\n")

    def note(self, text, kind="说明"):
        text = _brief(text, getattr(self, "_install_role", None))
        if text:
            self._wrapped(str(kind) + "：" + text)

    def options(self, choices):
        self.write("  选项：\n")
        for value, description in choices:
            self._wrapped(str(value) + ("：" + str(description) if description else ""), prefix="    ", continuation="      ")

    def message(self, text):
        for line in str(text).splitlines():
            stripped = _brief(line.strip(), getattr(self, "_install_role", None))
            if not stripped:
                if not line.strip():
                    self.write("\n")
            elif stripped.startswith("【") and stripped.endswith("】"):
                self.section(stripped[1:-1])
            else:
                self._wrapped(stripped)

    def ask(self, label, default="", secret=False, allow_empty=False):
        label = "".join(c for c in str(label) if c.isprintable())
        if "（" in label and label.endswith("）"):
            field_name, help_text = label.split("（", 1)
            help_text = help_text[:-1]
            if help_text not in ("秒", "分钟", "KB/s", "MB/s", "字节", "个"):
                label = field_name
                self.note(help_text)
        if "y/n" in label:
            label = label.replace("y/n", "").strip(" ？?；;")
            self.options([("y", "是"), ("n", "否")])
        label = "  " + label.rstrip("：:？?")
        if secret:
            prompt = str(label) + (" [回车保留]" if default else "") + "："
        else:
            prompt = str(label) + (f" [{default}]" if default != "" else "") + "："
        try:
            value = self._line(prompt, secret=secret).strip()
        except (EOFError, KeyboardInterrupt):
            self.note("已取消，终端设置已恢复。", "提示")
            raise SystemExit(1)
        if allow_empty and value == "!clear":
            return ""
        return value if value else default


def _field_type(default):
    if type(default) is bool:
        return "bool"
    if type(default) is int:
        return "int"
    if type(default) is float:
        return "float"
    if isinstance(default, (dict, list)):
        return "json"
    return "str"


def configure_provider(driver, old, base, ui):
    """Ask every DEFAULT_CONFIG key; CONFIG_FIELDS only customizes presentation."""
    defaults = driver.DEFAULT_CONFIG
    specs = getattr(driver, "CONFIG_FIELDS", {})
    if not isinstance(specs, dict) or set(specs) - set(defaults):
        raise ValueError("CONFIG_FIELDS必须是DEFAULT_CONFIG字段的子集")
    result = {key: old.get(key, value) for key, value in defaults.items()}
    while True:
        for key, fallback in defaults.items():
            spec = specs.get(key, {})
            if not isinstance(spec, dict):
                raise ValueError("字段声明必须为对象")
            kind = spec.get("type", _field_type(fallback))
            label = spec.get("label", key)
            secret = bool(spec.get("secret", "KEY" in key or "SECRET" in key or "PASSWORD" in key))
            current = result[key]
            if kind == "path" and spec.get("executable") and isinstance(current, str):
                current = shutil.which(current) or current
            if isinstance(current, str) and current.startswith("请"):
                current = ""
            if spec.get("help"):
                ui.note(spec["help"])
            if kind == "bool":
                ui.options([("y", "是"), ("n", "否")])
            elif "choices" in spec:
                ui.options([(str(choice), "") for choice in spec["choices"]])
            while True:
                if kind == "bool":
                    displayed = "y" if current else "n"
                elif kind == "csv":
                    displayed = ",".join(current) if isinstance(current, list) else ""
                elif kind == "json":
                    displayed = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
                else:
                    displayed = "" if current is None else str(current)
                text = ui.ask(label, displayed, secret=secret, allow_empty=not spec.get("required", False))
                try:
                    if kind == "bool":
                        val = str(text).lower()
                        if val not in ("y", "n", "true", "false", "1", "0", "yes", "no"):
                            raise ValueError("请输入y或n")
                        value = val in ("y", "true", "1", "yes")
                    elif kind == "int":
                        value = int(text)
                    elif kind == "float":
                        import math
                        value = float(text)
                        if not math.isfinite(value):
                            raise ValueError("必须为有限数值")
                    elif kind == "json":
                        value = json.loads(text)
                        if isinstance(fallback, (dict, list)) and type(value) is not type(fallback):
                            raise ValueError("JSON类型与默认值不一致")
                    elif kind == "csv":
                        value = [part.strip() for part in str(text).split(",") if part.strip()]
                    elif kind in ("str", "path"):
                        value = str(text)
                    else:
                        raise ValueError("不支持的字段类型：" + str(kind))
                    if spec.get("required") and (value is None or value == "" or value == []):
                        raise ValueError("不能为空")
                    if kind in ("int", "float"):
                        if "min" in spec and value < spec["min"]:
                            raise ValueError("数值低于允许范围")
                        if "max" in spec and value > spec["max"]:
                            raise ValueError("数值高于允许范围")
                    if "choices" in spec and value not in spec["choices"]:
                        raise ValueError("不在允许选项中")
                    if kind == "path" and spec.get("executable"):
                        path = Path(value)
                        if not path.is_absolute() or not path.is_file() or not os.access(str(path), os.X_OK):
                            raise ValueError("请输入存在且可执行的绝对路径")
                    result[key] = value
                    break
                except (ValueError, TypeError):
                    ui.message("  输入无效，请按字段类型和范围重新填写。\n")
        validator = getattr(driver, "validate_config", None)
        merged = dict(base)
        merged.update(result)
        if not callable(validator):
            return result
        try:
            validator(merged)
            return {key: merged[key] for key in defaults}
        except Exception as exc:
            # Don't echo custom-validator exception text: it may contain secrets.
            ui.message("  驱动参数校验未通过（" + type(exc).__name__ + "）。\n")
            if ui.ask("重新填写此驱动？y/n", "y").lower() != "y":
                raise ValueError("驱动配置取消") from None



# Wording compatibility for the previously delivered shell: shortening text here
# avoids requiring a shell replacement for presentation-only updates.
_BRIEF_TEXT = {
    "只安装采集服务，不会安装或修改隧道软件本身。": "不安装或修改隧道软件。",
    "从端固定使用 poll，仅供主端查询，不直接对接 MP。": "从端固定 poll，不向 MP 推送。",
    "查询接口将强制开启。请让主端访问本机查询地址。": "查询接口已强制开启。",
    "查询接口将强制开启。MP 检测方式请选择轮询。": "查询接口已开启；MP 选择轮询。",
    "可同时启用多个驱动，使用英文逗号分隔。": "多选用英文逗号分隔。",
    "只选择这台从端实际部署的服务；不要直接保留未使用的驱动。": "仅选择本机实际使用的驱动。",
    "填写 MoviePilot 的 Webhook 地址和 API_TOKEN；它们与采集端通信密钥不同。": "填写 MP Webhook 地址及 API_TOKEN。",
    "当前不推送，不需要填写 MP Webhook 地址或 API_TOKEN。": "当前不推送，跳过 MP 凭据。",
    "主端、所有从端和 MP 必须使用完全相同的 SECRET。": "所有采集端与 MP 使用同一 SECRET。",
    "从端或已配置远端的主端，请填写已有统一密钥；仅首次单机或无从端主端可生成。": "主从部署填写已有密钥；单机首次可回车生成。",
    "输入隐藏；有默认值时，直接回车保留。": None,
    "poll 模式必须开启查询接口。": "poll 强制开启查询接口。",
    "从端需让主端可访问；单机/主端查询接口需让 MP 可访问。": "确保主端或 MP 能访问该接口。",
    "0.0.0.0 表示监听本机所有 IPv4 地址，不会自动修改防火墙。": "仅支持 IPv4；不修改防火墙。",
    "按请求采集；查询缓存未到期时复用，不启动后台采集推送循环。": "按请求采集，缓存期内复用。",
    "后台周期采集；状态变化或保活到期才发送推送。": "变化推送，定期保活。",
    "采集间隔 1~3600 秒；保活间隔 60~1500 秒。": "采集 1~3600 秒；保活 60~1500 秒。",
    "仅检查配置和试采，不发送 MP 通知、不分配发布版本号。": "试采，不向 MP 推送。",
    "确认后写入配置，启用开机启动，并启动或重启服务。": "确认后部署、设为自启并重启服务。",
    "现有程序/配置/服务文件会备份；不会删除或重置 seq 状态文件。": "备份原文件，保留 seq。",
    "不会自动开放端口；请只允许 MoviePilot 所在的可信网络访问。": "仅允许可信网络访问。",
}


def _brief(text, role=None):
    text = str(text)
    if role == "standalone":
        short = {
            "主端、所有从端和 MP 必须使用完全相同的 SECRET。": "与 MP 插件通信密钥一致。",
            "从端或已配置远端的主端，请填写已有统一密钥；仅首次单机或无从端主端可生成。": "首次可回车生成；更新回车保留。",
            "从端需让主端可访问；单机/主端查询接口需让 MP 可访问。": "确保 MP 能访问该接口。",
        }
        if text in short:
            return short[text]
    return _BRIEF_TEXT.get(text, text)


class TerminalWriter:
    """Output adapter for existing Provider tty.write hooks."""
    def __init__(self, ui): self.ui = ui
    def write(self, text):
        self.ui.message(text)
        return len(text)
    def flush(self): pass
    def close(self): pass


def configure_cluster(old, ui):
    import re
    ui.section("采集端角色")
    ui.options([("standalone", "单机直连 MP"), ("master", "主端汇总"), ("slave", "从端供主端查询")])
    while True:
        role = ui.ask("角色", old.get("ROLE", "standalone")).lower()
        if role in ("standalone", "master", "slave"):
            break
        ui.note("请输入 standalone / master / slave。", "错误")
    ui._install_role = role

    if role == "standalone":
        # Single-machine identity is not a user-facing setting. Preserve an
        # existing valid value; fresh installations use the framework default.
        cid = old.get("COLLECTOR_ID", "local")
        if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", cid):
            cid = "local"
    else:
        ui.section("来源 ID")
        ui.note("1~32 位字母、数字、下划线或连字符。")
        ui.note("不与从端 ID 重复。" if role == "master" else "主端添加本机时，填写相同 ID。")
        default_id = old.get("COLLECTOR_ID", "main" if role == "master" else "router-b")
        while True:
            cid = ui.ask("主端 ID" if role == "master" else "从端 ID", default_id)
            if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", cid):
                break
            ui.note("ID 格式无效。", "错误")

    def number(label, default, lo, hi):
        while True:
            try:
                value = int(ui.ask(label, str(default)))
                if lo <= value <= hi:
                    return value
            except ValueError:
                pass
            ui.note(f"请输入 {lo}~{hi} 的整数。", "错误")

    ui.section("失败缓存")
    ui.note("1~900 秒；不等同于 MP 失联释放时间。")
    ttl = number("缓存有效期（秒）", old.get("SOURCE_CACHE_TTL", 300), 1, 900)
    remotes = []
    if role == "master":
        ui.section("从端连接")
        interval = number("查询间隔（秒）", min(old.get("REMOTE_INTERVAL", 10), ttl), 1, min(ttl, 300))
        timeout = number("请求超时（秒）", old.get("REMOTE_TIMEOUT", 3), 1, 30)
        ui.note("数量不含本机；减少数量将移除末尾配置。")
        count = number("从端数量", len(old.get("REMOTE_SOURCES", [])), 0, 16)
        previous = old.get("REMOTE_SOURCES", [])
        for i in range(count):
            prior = previous[i] if i < len(previous) else {}
            ui.section(f"从端 {i+1}")
            ui.note("ID 与从端一致；地址填写从端根 URL。")
            remotes.append({"id": ui.ask(f"从端 {i+1} ID", prior.get("id", f"router-{i+1}")),
                            "url": ui.ask(f"从端 {i+1} 地址", prior.get("url", ""))})
    else:
        interval, timeout = min(10, ttl), 3
    return {"ROLE": role, "COLLECTOR_ID": cid, "SOURCE_CACHE_TTL": ttl,
            "REMOTE_INTERVAL": interval, "REMOTE_TIMEOUT": timeout, "REMOTE_SOURCES": remotes}


def summary_text(*, poll, shared_secret, api_url, selected, query_enabled,
                 peers, config_file, platform, unavailable=(), role="standalone",
                 collector_id="local", cache_ttl=300, remotes=()):
    """One closing section, grouped into MP/parent connection and maintenance."""
    def safe(value):
        return "".join(c for c in str(value) if c.isprintable())
    lines = ["", "【安装完成 · 配置与维护】"]
    if role == "slave":
        lines += ["  主端接入：", f"    从端 ID：{safe(collector_id)}",
                  f"    查询地址：{safe(api_url)}", f"    通信密钥：{safe(shared_secret)}"]
    else:
        lines += ["  MP 配置：", "    检测方式：" + ("轮询" if poll else "Webhook 推送"),
                  "    通信密钥：" + safe(shared_secret),
                  "    采集端 API：" + (safe(api_url) if query_enabled else "留空"),
                  "    检测/补偿周期：*/1 * * * *"]
        if not poll:
            lines.append("    主动心跳：0（使用采集端保活）")
        tokens = []
        for peer in peers:
            if peer.get("provider") == "easytier" and peer.get("cost") == "Local":
                continue
            token = safe(peer.get("hostname") or str(peer.get("ipv4") or "").split("/", 1)[0])
            if token and token not in tokens:
                tokens.append(token)
        lines.append("    监控主机候选（排除本机恒在线节点）：")
        lines.extend("      " + token for token in tokens[:20])
        if not tokens:
            lines.append("      暂无，来源恢复后查询。")
        elif len(tokens) > 20:
            lines.append("      其余节点通过查询接口查看。")
        lines.append("    下载器及让位方式：在 MP 中选择。")
        if role == "master":
            lines.append("    提示：使用带来源后缀的完整主机名。")
    lines += ["", "  维护操作：", "    配置：" + safe(config_file)]
    if platform == "openwrt":
        commands = ["/etc/init.d/tunnel-api status", "/etc/init.d/tunnel-api restart",
                    "/etc/init.d/tunnel-api stop", "/etc/init.d/tunnel-api start", "logread -e tunnel"]
    else:
        commands = ["systemctl status tunnel-api", "systemctl restart tunnel-api",
                    "systemctl stop tunnel-api", "systemctl start tunnel-api", "journalctl -u tunnel-api -f"]
    labels = ["状态", "重启", "停止", "启动", "日志"]
    lines.extend("    " + label + "：" + cmd for label, cmd in zip(labels, commands))
    if unavailable:
        lines += ["", "  注意：暂不可用来源 " + ", ".join(safe(x) for x in unavailable)]
    return "\n".join(lines) + "\n"

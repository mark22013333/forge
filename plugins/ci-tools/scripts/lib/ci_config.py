#!/usr/bin/env python3
"""ci-tools 設定檔讀取器：servers.yml（使用者層）與 .ci-tools.yml（專案層）。

只用標準函式庫，不依賴 PyYAML 或 yq。支援的 YAML 子集：
  - 以空白縮排的巢狀 mapping（key: value）
  - 區塊清單（- 純量、- key: value 開頭的 mapping）
  - 行內清單 [a, b, "c"]
  - 單／雙引號字串、# 註解（引號內的 # 不算註解）
不支援：多行字串（| >）、錨點與別名、行內 mapping {a: 1}、多文件。
設定檔超出這個子集時會直接報錯，不會猜。

用法（輸出給 shell 讀，欄位以 \x1f（ASCII Unit Separator）分隔，shell 端用 IFS=$'\x1f' read）：
  ci_config.py servers <servers.yml>
      每個 server 一行：name url collection pat_env ssh_key ssh_url api_version
  ci_config.py agents <servers.yml> <server>
      每個 agent 一行：name gradle_user_home npm_cache cached_gradle_versions(逗號分隔) jdk(k=v 逗號分隔)
  ci_config.py project <.ci-tools.yml>
      專案層純量設定，每行 key<TAB>value（清單以逗號連接）
  ci_config.py repos <.ci-tools.yml> [servers.yml]
      每個 repo 一行：name role gitea_url ado_url branches ssh_key
      ado_url：repo 自己有 ado_url 就用它；否則用 server 的 ssh_url 樣式代入
               {collection} {project} {repo}
  ci_config.py project-server <.ci-tools.yml> <servers.yml>
      專案使用的 server（server 欄位，省略時用 default_server），欄位同 servers；找不到時 exit 3
  ci_config.py get <file> <a.b.c>
      取單一值；清單以逗號連接；不存在時 exit 3
  ci_config.py git-urls <file>
      列出所有 key 以 gitea_url 結尾的值（相容舊版單層 .ci-tools.yml）
"""

from __future__ import annotations

import os
import re
import sys

DEFAULT_PAT_ENV = "ADO_PAT"
DEFAULT_API_VERSION = "6.0"


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- YAML 子集解析

def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _scalar(text: str):
    t = text.strip()
    if t == "":
        return None
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        inner = t[1:-1]
        return inner.replace("''", "'") if t[0] == "'" else inner
    if t.startswith("{"):
        raise ConfigError(f"不支援行內 mapping：{t}")
    if t[0] in "|>&*":
        raise ConfigError(f"不支援多行字串、錨點或別名：{t}")
    if t.startswith("["):
        if not t.endswith("]"):
            raise ConfigError(f"行內清單沒有結尾的 ]：{t}")
        body = t[1:-1].strip()
        if not body:
            return []
        items, buf, quote = [], "", None
        for ch in body:
            if quote:
                buf += ch
                if ch == quote:
                    quote = None
            elif ch in ("'", '"'):
                quote = ch
                buf += ch
            elif ch == ",":
                items.append(_scalar(buf))
                buf = ""
            else:
                buf += ch
        items.append(_scalar(buf))
        return items
    return t


def _split_key(text: str):
    """回傳 (key, rest)；不是 key: value 形式時回傳 None。"""
    t = text
    if t[:1] in ("'", '"'):
        end = t.find(t[0], 1)
        if end < 0:
            return None
        key, after = t[1:end], t[end + 1:]
        if not after.startswith(":"):
            return None
        return key, after[1:]
    idx = t.find(":")
    while idx >= 0:
        if idx + 1 == len(t) or t[idx + 1] in " \t":
            return t[:idx].strip(), t[idx + 1:]
        idx = t.find(":", idx + 1)
    return None


def parse_yaml(text: str):
    lines = []
    for no, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ConfigError(f"第 {no} 行用了 TAB 縮排")
        body = _strip_comment(raw).rstrip()
        if body.strip() in ("", "---"):
            continue
        lines.append((len(body) - len(body.lstrip(" ")), body.strip(), no))

    pos = 0

    def parse_block(indent: int):
        nonlocal pos
        if pos >= len(lines):
            return None
        if lines[pos][1].startswith("- ") or lines[pos][1] == "-":
            return parse_list(lines[pos][0])
        return parse_map(lines[pos][0])

    def parse_map(indent: int, first: tuple | None = None):
        nonlocal pos
        result: dict = {}
        pending = [first] if first else []
        while pending or pos < len(lines):
            if pending:
                ind, content, no = pending.pop()
            else:
                ind, content, no = lines[pos]
                if ind < indent:
                    break
                if ind > indent:
                    raise ConfigError(f"第 {no} 行縮排不一致")
                if content.startswith("- "):
                    break
                pos += 1
            kv = _split_key(content)
            if kv is None:
                raise ConfigError(f"第 {no} 行不是 key: value：{content}")
            key, rest = kv
            if rest.strip():
                result[key] = _scalar(rest)
            elif pos < len(lines) and (
                lines[pos][0] > ind
                or (lines[pos][0] == ind and lines[pos][1].startswith("- "))
            ):
                result[key] = parse_block(lines[pos][0])
            else:
                result[key] = None
        return result

    def parse_list(indent: int):
        nonlocal pos
        result = []
        while pos < len(lines):
            ind, content, no = lines[pos]
            if ind < indent or not (content.startswith("- ") or content == "-"):
                break
            if ind > indent:
                raise ConfigError(f"第 {no} 行縮排不一致")
            pos += 1
            item = content[1:].strip()
            if not item:
                result.append(parse_block(lines[pos][0]) if pos < len(lines) and lines[pos][0] > ind else None)
                continue
            kv = None if item.startswith("[") else _split_key(item)
            if kv is not None:
                # 「- key: value」開頭的 mapping：後續同層 key 的縮排＝「- 」之後的位置
                child_indent = ind + 2
                result.append(parse_map(child_indent, first=(child_indent, item, no)))
            else:
                result.append(_scalar(item))
        return result

    if not lines:
        return {}
    data = parse_block(lines[0][0])
    if pos < len(lines):
        raise ConfigError(f"第 {lines[pos][2]} 行無法解析：{lines[pos][1]}")
    return data


def load(path: str):
    try:
        with open(os.path.expanduser(path), encoding="utf-8-sig") as fh:
            data = parse_yaml(fh.read())
    except OSError as exc:
        raise ConfigError(f"讀不到 {path}：{exc.strerror}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 的最外層必須是 mapping")
    return data


# ---------------------------------------------------------------- 輸出輔助

def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(_fmt(v) for v in value)
    if isinstance(value, dict):
        return ",".join(f"{k}={_fmt(v)}" for k, v in value.items())
    return str(value)


SEP = "\x1f"  # 欄位分隔字元（ASCII Unit Separator）。不用 TAB：bash 的 read 會把連續 TAB 合併，空欄位就錯位


def _row(*cols) -> None:
    print(SEP.join(_fmt(c).replace(SEP, " ") for c in cols))


def _servers(path: str) -> dict:
    data = load(path)
    servers = data.get("servers") or {}
    if not isinstance(servers, dict):
        raise ConfigError("servers 必須是以 server 名稱為 key 的 mapping")
    return servers


def _expand_home(value):
    return os.path.expanduser(value) if isinstance(value, str) and value.startswith("~") else value


def _pat_env(s: dict) -> str:
    """權杖環境變數名稱；不合法的名稱（例如含 -）會讓 shell 的 ${!name} 中止，這裡先擋下"""
    name = s.get("pat_env") or DEFAULT_PAT_ENV
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
        raise ConfigError(f"pat_env 不是合法的環境變數名稱：{name}")
    return name


# ---------------------------------------------------------------- 子命令

def cmd_servers(path: str) -> int:
    for name, s in _servers(path).items():
        s = s or {}
        _row(name, s.get("url"), s.get("collection"), _pat_env(s),
             _expand_home(s.get("ssh_key")), s.get("ssh_url"), s.get("api_version") or DEFAULT_API_VERSION)
    return 0


def cmd_agents(path: str, server: str) -> int:
    servers = _servers(path)
    if server not in servers:
        raise ConfigError(f"servers.yml 沒有 server：{server}")
    for a in (servers[server] or {}).get("agents") or []:
        a = a or {}
        _row(a.get("name"), a.get("gradle_user_home"), a.get("npm_cache"),
             a.get("cached_gradle_versions") or [], a.get("jdk") or {})
    return 0


def cmd_project(path: str) -> int:
    for key, value in load(path).items():
        if key == "repos":
            continue
        _row(key, value)
    return 0


def _project_server(proj: dict, servers_path: str | None) -> tuple[str | None, dict]:
    """依 .ci-tools.yml 的 server（省略時用 servers.yml 的 default_server）找出 server 設定。"""
    if not servers_path or not os.path.isfile(os.path.expanduser(servers_path)):
        return None, {}
    name = proj.get("server") or load(servers_path).get("default_server")
    if not name:
        return None, {}
    servers = _servers(servers_path)
    if name not in servers:
        raise ConfigError(f"servers.yml 沒有 server：{name}")
    return name, servers[name] or {}


def cmd_project_server(path: str, servers_path: str) -> int:
    name, s = _project_server(load(path), servers_path)
    if not name:
        return 3
    _row(name, s.get("url"), s.get("collection"), _pat_env(s),
         _expand_home(s.get("ssh_key")), s.get("ssh_url"), s.get("api_version") or DEFAULT_API_VERSION)
    return 0


def cmd_repos(path: str, servers_path: str | None) -> int:
    proj = load(path)
    _, server = _project_server(proj, servers_path)
    repos = proj.get("repos") or []
    if not isinstance(repos, list):
        raise ConfigError("repos 必須是清單")
    for r in repos:
        if not isinstance(r, dict) or not r.get("name"):
            raise ConfigError("repos 的每一項都要有 name")
        ado_url = r.get("ado_url")
        if not ado_url and server.get("ssh_url"):
            ado_url = (str(server["ssh_url"])
                       .replace("{collection}", str(server.get("collection") or ""))
                       .replace("{project}", str(proj.get("ado_project") or ""))
                       .replace("{repo}", str(r.get("ado_repo") or r["name"])))
        key = r.get("ssh_key") or proj.get("ssh_key") or server.get("ssh_key")
        _row(r["name"], r.get("role") or "main", r.get("gitea_url"), ado_url,
             r.get("branches") or [], _expand_home(key))
    return 0


def cmd_get(path: str, dotted: str) -> int:
    node = load(path)
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return 3
    print(_fmt(node))
    return 0


def cmd_git_urls(path: str) -> int:
    seen = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k).endswith("gitea_url") and isinstance(v, str) and v not in seen:
                    seen.append(v)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(load(path))
    for url in seen:
        print(url)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "servers":
            return cmd_servers(args[0])
        if cmd == "agents" and len(args) == 2:
            return cmd_agents(args[0], args[1])
        if cmd == "project":
            return cmd_project(args[0])
        if cmd == "repos":
            return cmd_repos(args[0], args[1] if len(args) > 1 else None)
        if cmd == "project-server" and len(args) == 2:
            return cmd_project_server(args[0], args[1])
        if cmd == "get" and len(args) == 2:
            return cmd_get(args[0], args[1])
        if cmd == "git-urls":
            return cmd_git_urls(args[0])
    except ConfigError as exc:
        print(f"設定檔錯誤：{exc}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

import json
import os
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=None):
    p = Path(path) if path else ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load():
    load_env()
    with open(ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def email_policy(cfg, country):
    """Return (allowed, reason) for cold email to a business in `country` (ISO-2)."""
    c = (country or "").upper()
    p = cfg["policy"]
    if c in p["email_ok"]:
        return True, "opt-out regime"
    if c in p["email_b2b"]:
        return True, "B2B published address"
    return False, f"cold email needs prior consent in {c or 'unknown country'}"


def _toml(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def save_values(changes, path=None):
    """Update existing keys in config.toml in place, keeping comments. changes = {(section, key): value}."""
    path = path or ROOT / "config.toml"
    lines, out, section, i = path.read_text(encoding="utf-8").splitlines(), [], "", 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"\s*\[(\w+)\]", line)
        if m:
            section = m.group(1)
        k = re.match(r"(\s*)(\w+)\s*=\s*(.*)", line)
        if k and (section, k.group(2)) in changes:
            rest = k.group(3)
            if rest.startswith("[") and "]" not in rest:  # multi-line list: skip to its end
                while "]" not in lines[i]:
                    i += 1
                comment = ""
            else:
                c = re.search(r"\s+#[^\"\]]*$", rest)
                comment = c.group(0) if c else ""
            line = f"{k.group(1)}{k.group(2)} = {_toml(changes[(section, k.group(2))])}{comment}"
        out.append(line)
        i += 1
    text = "\n".join(out) + "\n"
    tomllib.loads(text)  # refuse to write a broken file
    path.write_text(text, encoding="utf-8")


def save_env(values, path=None):
    """Write mailbox settings to .env (blank values keep what is already there)."""
    path = path or ROOT / ".env"
    current = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                current[k.strip()] = v.strip()
    for k, v in values.items():
        if v and "\n" not in v and "\r" not in v:
            current[k] = v
            os.environ[k] = v
    path.write_text("".join(f"{k}={v}\n" for k, v in current.items()), encoding="utf-8")

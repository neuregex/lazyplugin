"""The project graph ("graphify"): a compact symbol index of a generated
plugin so EDITS send only what matters.

Without this, every "change X" iteration reships the whole project — token
cost grows with project size and the flat price goes underwater. With it, an
edit sends: the full symbol MAP (cheap: ~50 tokens/file) + the BODIES of only
the files relevant to the instruction (+ plugin.yml and the main class, always)
— bounded cost regardless of how big the plugin has grown.

Nodes = files (with their classes/methods/commands/events/config keys);
edges = project-internal imports (used to pull 1-hop signature context).
Extraction is regex-based — fine for our own generated code style; swap in a
real parser only if user-imported projects ever land.
"""
from __future__ import annotations

import json
import os
import re

INDEX_NAME = "forge.json"

_CLASS_RE = re.compile(r"(?:public\s+)?(?:final\s+)?(?:abstract\s+)?(class|interface|enum|record)\s+(\w+)")
_METHOD_RE = re.compile(r"(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\], .?]+\s+(\w+)\s*\(")
_EVENT_RE = re.compile(r"@EventHandler[\s\S]{0,120}?on\w*\(\s*(\w+)\s+\w+\s*\)")
_GETCMD_RE = re.compile(r"getCommand\(\s*\"(\w+)\"")
_CFGKEY_RE = re.compile(r"getConfig\(\)\s*\.\s*get\w*\(\s*\"([\w.\-]+)\"")
_IMPORT_RE = re.compile(r"^import\s+(io\.lazyplugin[\w.]*|com\.example[\w.]*)\.(\w+);", re.MULTILINE)
_YMLCMD_RE = re.compile(r"^  (\w+):", re.MULTILINE)


def _java_files(project_dir: str):
    src = os.path.join(project_dir, "src")
    for root, _dirs, files in os.walk(src):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, project_dir).replace("\\", "/")
            yield rel, full


def build_index(project_dir: str, request: str = "", mc_version: str = "1.21") -> dict:
    """Scan the project and write forge.json. Returns the index."""
    files = []
    for rel, full in _java_files(project_dir):
        try:
            text = open(full, encoding="utf-8").read()
        except OSError:
            continue
        entry: dict = {"path": rel}
        if rel.endswith(".java"):
            kinds = _CLASS_RE.findall(text)
            entry["classes"] = [name for _k, name in kinds]
            entry["methods"] = sorted(set(_METHOD_RE.findall(text)))[:20]
            entry["events"] = sorted(set(_EVENT_RE.findall(text)))
            entry["commands"] = sorted(set(_GETCMD_RE.findall(text)))
            entry["config_keys"] = sorted(set(_CFGKEY_RE.findall(text)))[:15]
            entry["uses"] = sorted({cls for _pkg, cls in _IMPORT_RE.findall(text)})
            entry["main"] = "extends JavaPlugin" in text
        elif rel.endswith((".yml", ".yaml")):
            if rel.endswith("plugin.yml"):
                m = re.search(r"commands:\n((?:  \w+:[\s\S]*?)(?=\n\w|\Z))", text)
                entry["commands"] = _YMLCMD_RE.findall(m.group(1)) if m else []
            entry["kind"] = "yml"
        files.append(entry)
    index = {"request": request, "mc": mc_version, "files": files}
    with open(os.path.join(project_dir, INDEX_NAME), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1, ensure_ascii=False)
    return index


def load_index(project_dir: str) -> dict | None:
    p = os.path.join(project_dir, INDEX_NAME)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def format_map(index: dict) -> str:
    """The whole-project symbol map the model always sees (cheap)."""
    out = []
    for f in index["files"]:
        bits = []
        if f.get("classes"):
            bits.append("classes: " + ", ".join(f["classes"]))
        if f.get("commands"):
            bits.append("commands: " + ", ".join(f["commands"]))
        if f.get("events"):
            bits.append("events: " + ", ".join(f["events"]))
        if f.get("methods"):
            bits.append("methods: " + ", ".join(f["methods"][:12]))
        if f.get("config_keys"):
            bits.append("config: " + ", ".join(f["config_keys"]))
        tag = " [MAIN]" if f.get("main") else ""
        out.append(f"- {f['path']}{tag}" + (" — " + "; ".join(bits) if bits else ""))
    return "\n".join(out)


_WORD = re.compile(r"[a-z0-9]{3,}")


def relevant_files(index: dict, instruction: str, k: int = 4) -> list[str]:
    """Paths whose bodies the edit prompt should include: lexical score of the
    instruction vs each node's symbols, then +1-hop over import edges, plus the
    anchors (plugin.yml and the main class) always."""
    q = set(_WORD.findall(instruction.lower()))
    scored = []
    by_class: dict[str, str] = {}
    for f in index["files"]:
        for c in f.get("classes", []):
            by_class[c] = f["path"]
        hay = " ".join([f["path"]] + f.get("classes", []) + f.get("methods", [])
                       + f.get("commands", []) + f.get("events", []) + f.get("config_keys", []))
        terms = set(_WORD.findall(hay.lower()))
        score = len(q & terms)
        if score:
            scored.append((score, f))
    scored.sort(key=lambda s: -s[0])
    picked = []
    for _s, f in scored[:k]:
        picked.append(f["path"])
        for used in f.get("uses", []):                  # 1-hop over the import edges
            dep = by_class.get(used)
            if dep and dep not in picked and len(picked) < k + 2:
                picked.append(dep)
    for f in index["files"]:                            # anchors always ride along
        if (f.get("main") or f["path"].endswith("plugin.yml")) and f["path"] not in picked:
            picked.append(f["path"])
    return picked

"""The Forge pipeline: request -> RAG -> LLM -> project -> Maven -> jar.

Every run returns a full token/cost report — those numbers decide whether the
hosted version has margin (stay private) or is thin (open-source it).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from .models import resolve
from .retrieve import format_cards, retrieve
from .templates import FIX_PROMPT, PAPER_API, POM_XML, SYSTEM_PROMPT

_FILE_RE = re.compile(r"=== FILE: (.+?) ===\n(.*?)(?=\n=== FILE: |\Z)", re.DOTALL)

# Per-1M-token rates. Normally supplied by the model registry; these env vars
# override everything, for a provider whose pricing we do not track.
_ENV_RATE_IN = os.environ.get("LAZYPLUGIN_RATE_IN")
_ENV_RATE_OUT = os.environ.get("LAZYPLUGIN_RATE_OUT")

# Maven lookup, in order: explicit env, whatever is on PATH, a no-sudo install
# in the home directory. Set LAZYPLUGIN_MAVEN if yours lives somewhere odd.
_MAVEN_CANDIDATES = tuple(c for c in (
    os.environ.get("LAZYPLUGIN_MAVEN"),
    shutil.which("mvn"),
    shutil.which("mvn.cmd"),
    os.path.expanduser("~/maven/bin/mvn"),
) if c)
_DEFAULT_MAVEN = next((p for p in _MAVEN_CANDIDATES if os.path.exists(p)), "mvn")


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    rate_in: float | None = None      # USD per 1M, from the model registry
    rate_out: float | None = None

    def add(self, response) -> None:
        self.calls += 1
        u = getattr(response, "usage", None)
        if u:
            self.prompt_tokens += getattr(u, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(u, "completion_tokens", 0) or 0

    @property
    def cost_usd(self) -> float | None:
        """None when nobody knows the price — a local model reports 0.0, an
        untracked hosted model reports nothing rather than a made-up number."""
        ri = float(_ENV_RATE_IN) if _ENV_RATE_IN else self.rate_in
        ro = float(_ENV_RATE_OUT) if _ENV_RATE_OUT else self.rate_out
        if ri is None or ro is None:
            return None
        return (self.prompt_tokens * ri + self.completion_tokens * ro) / 1_000_000


@dataclass
class ForgeResult:
    ok: bool
    project_dir: str
    jar_path: str | None
    files: list[str]
    attempts: int
    usage: Usage
    duration_s: float
    error: str | None = None
    compile_log_tail: str = ""
    events: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:40] or "plugin").rstrip("-")


def _client(resolved, api_key: str | None = None):
    """An OpenAI-protocol client pointed at whichever provider was resolved."""
    from openai import OpenAI

    from .models import credentials
    key, base_url = credentials(resolved, api_key)
    return OpenAI(api_key=key, base_url=base_url)


def _parse_files(text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for path, content in _FILE_RE.findall(text or ""):
        path = path.strip().replace("\\", "/").lstrip("/")
        if ".." in path or path.startswith(("~", "pom.xml")):
            continue                                    # never escape the project / touch our pom
        # yml files must live in resources or Maven won't pack them into the jar
        if path.endswith((".yml", ".yaml")) and not path.startswith("src/"):
            path = "src/main/resources/" + os.path.basename(path)
        # strip a stray markdown fence the model sometimes wraps a file in
        content = re.sub(r"^```[a-z]*\n|\n```\s*$", "", content.strip("\n"), flags=re.MULTILINE)
        files[path] = content.rstrip() + "\n"
    return files


def _write_project(project_dir: str, artifact: str, mc_version: str, files: dict[str, str]) -> None:
    os.makedirs(project_dir, exist_ok=True)
    src = os.path.join(project_dir, "src")
    if os.path.isdir(src):
        shutil.rmtree(src, ignore_errors=True)          # fix rounds re-emit everything
    pom = POM_XML.format(artifact=artifact, paper_version=PAPER_API[mc_version])
    with open(os.path.join(project_dir, "pom.xml"), "w", encoding="utf-8") as f:
        f.write(pom)
    for rel, content in files.items():
        dest = os.path.join(project_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)


_INFRA_MARKERS = ("zip END header not found", "Could not resolve dependencies",
                  "Could not transfer", "Connection timed out", "error reading C:",
                  "Fatal error compiling", "release version 21 not supported")
_CORRUPT_JAR_RE = re.compile(r"error reading (.+?\.jar)")


def _purge_corrupt_artifacts(log: str) -> int:
    """A truncated jar in ~/.m2 poisons every build — delete its version dir so
    Maven re-downloads. Returns how many artifacts were purged."""
    purged = 0
    for jar in set(_CORRUPT_JAR_RE.findall(log)):
        vdir = os.path.dirname(jar.strip())
        if os.sep + ".m2" + os.sep in vdir and os.path.isdir(vdir):
            shutil.rmtree(vdir, ignore_errors=True)
            purged += 1
    return purged


def _is_infra_error(log: str) -> bool:
    """Repo/download problems the LLM can't fix — never burn fix rounds on them."""
    return any(m in log for m in _INFRA_MARKERS)


def _compile(project_dir: str, maven_bin: str | None = None) -> tuple[bool, str, str | None]:
    """(ok, log_tail, jar_path)"""
    mvn = maven_bin or os.environ.get("FORGE_MAVEN") or (
        _DEFAULT_MAVEN if os.path.exists(_DEFAULT_MAVEN) else "mvn")
    env = os.environ.copy()
    # service processes (pm2) often carry a stale JAVA_HOME — pin it explicitly
    java_home = os.environ.get("FORGE_JAVA_HOME") or next(
        (p for p in ("/usr/lib/jvm/java-21-openjdk-amd64",) if os.path.isdir(p)), None)
    if java_home:
        env["JAVA_HOME"] = java_home
        env["PATH"] = os.path.join(java_home, "bin") + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [mvn, "-q", "-B", "-DskipTests", "package"],
        cwd=project_dir, capture_output=True, text=True, timeout=600, env=env,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        # keep the ERROR lines (what the fix round needs) + a generic tail
        err_lines = [ln for ln in out.splitlines() if "ERROR" in ln or "error:" in ln]
        tail = "\n".join(err_lines[:60]) or out[-4000:]
        return False, tail, None
    target = os.path.join(project_dir, "target")
    jars = [f for f in os.listdir(target) if f.endswith(".jar") and not f.startswith("original-")] \
        if os.path.isdir(target) else []
    if not jars:
        return False, "build succeeded but no jar was produced", None
    return True, "", os.path.join(target, jars[0])


def forge(request: str, mc_version: str = "1.21", out_dir: str | None = None,
          api_key: str | None = None, base_url: str | None = None,
          model: str | None = None, provider: str | None = None,
          max_fix_rounds: int = 2, k: int = 6,
          maven_bin: str | None = None, on_event=None) -> ForgeResult:
    """Full pipeline. `on_event(str)` gets human-readable progress lines."""
    t0 = time.time()
    events: list[str] = []

    def emit(msg: str):
        events.append(msg)
        if on_event:
            on_event(msg)

    if mc_version not in PAPER_API:
        raise ValueError(f"unsupported version {mc_version!r} (have {list(PAPER_API)})")
    picked = resolve(model, provider, api_key, base_url)
    client = _client(picked, api_key)
    mdl = picked.model
    usage = Usage(rate_in=picked.price_in, rate_out=picked.price_out)

    cards = retrieve(request, k=k)
    emit(f"retrieved {len(cards)} recipe cards: " + ", ".join(c["id"] for c in cards))
    system = SYSTEM_PROMPT.format(mc=mc_version, cards=format_cards(cards))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": request}]

    project_dir = out_dir or os.path.join("forge_out", _slug(request))
    artifact = _slug(request)
    files: dict[str, str] = {}
    log_tail = ""
    jar = None

    # hard token budget per forge — bounds the worst case so a flat credit
    # price can never be underwater (battery max was ~28k total)
    max_tokens_run = int(os.environ.get("LAZYPLUGIN_MAX_TOKENS", "80000"))

    for attempt in range(1, max_fix_rounds + 2):
        if usage.prompt_tokens + usage.completion_tokens > max_tokens_run:
            log_tail = f"token budget exceeded ({max_tokens_run}) — stopping"
            emit(log_tail)
            break
        emit(f"attempt {attempt}: calling the model...")
        resp = client.chat.completions.create(
            model=mdl, temperature=0.4, max_tokens=8192, messages=messages,
            # V4 thinking mode would eat this budget and return an empty
            # message (measured on the build engine). Same knob here.
            extra_body={"thinking": {"type": "disabled"}})
        usage.add(resp)
        text = resp.choices[0].message.content or ""
        parsed = _parse_files(text)
        if not parsed:
            log_tail = "model produced no parseable files"
            emit(log_tail)
            break
        files = parsed
        _write_project(project_dir, artifact, mc_version, files)
        emit(f"attempt {attempt}: {len(files)} files -> compiling...")
        ok, log_tail, jar = _compile(project_dir, maven_bin)
        if not ok and _is_infra_error(log_tail):
            purged = _purge_corrupt_artifacts(log_tail)
            emit(f"infra error (not the model's code) — purged {purged} corrupt artifact(s), recompiling...")
            ok, log_tail, jar = _compile(project_dir, maven_bin)
        if ok:
            emit(f"compiled OK -> {jar}")
            from .graph import build_index
            build_index(project_dir, request, mc_version)      # the edit graph
            break
        if _is_infra_error(log_tail):
            emit("compile blocked by an infrastructure error — not burning fix rounds on it")
            break
        emit(f"attempt {attempt}: compile FAILED ({len(log_tail.splitlines())} error lines)")
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": FIX_PROMPT.format(errors=log_tail[:6000])})
    else:
        pass

    result = ForgeResult(
        ok=jar is not None,
        project_dir=os.path.abspath(project_dir),
        jar_path=os.path.abspath(jar) if jar else None,
        files=sorted(files),
        attempts=usage.calls,
        usage=usage,
        duration_s=round(time.time() - t0, 1),
        error=None if jar else log_tail,
        compile_log_tail=log_tail,
        events=events,
    )
    _log_run(request, mc_version, mdl, result)
    return result


# --- edit flow: change an existing forged project --------------------------

def _merge_files(project_dir: str, files: dict[str, str]) -> None:
    """Write ONLY the returned files over the existing project (never wipes)."""
    for rel, content in files.items():
        dest = os.path.join(project_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)


def _read_bodies(project_dir: str, paths: list[str]) -> str:
    parts = []
    for rel in paths:
        full = os.path.join(project_dir, *rel.split("/"))
        try:
            parts.append(f"=== FILE: {rel} ===\n" + open(full, encoding="utf-8").read())
        except OSError:
            continue
    return "\n".join(parts)


_ERRFILE_RE = re.compile(r"(src/main/[\w/.-]+\.java)")


def forge_edit(project_dir: str, instruction: str,
               api_key: str | None = None, base_url: str | None = None,
               model: str | None = None, provider: str | None = None, max_fix_rounds: int = 2,
               maven_bin: str | None = None, on_event=None) -> ForgeResult:
    """Edit an existing forged project: symbol map + relevant bodies only —
    bounded token cost regardless of project size (the graphify)."""
    from .graph import build_index, format_map, load_index, relevant_files
    from .retrieve import format_cards, retrieve
    from .templates import EDIT_FIX_PROMPT, EDIT_SYSTEM

    t0 = time.time()
    events: list[str] = []

    def emit(msg: str):
        events.append(msg)
        if on_event:
            on_event(msg)

    index = load_index(project_dir) or build_index(project_dir)
    mc_version = index.get("mc", "1.21")
    picked = resolve(model, provider, api_key, base_url)
    client = _client(picked, api_key)
    mdl = picked.model
    usage = Usage(rate_in=picked.price_in, rate_out=picked.price_out)
    max_tokens_run = int(os.environ.get("LAZYPLUGIN_MAX_TOKENS", "80000"))

    rel = relevant_files(index, instruction)
    emit(f"edit context: {len(rel)}/{len(index['files'])} files -> " + ", ".join(rel))
    system = EDIT_SYSTEM.format(
        mc=mc_version, project_map=format_map(index),
        file_bodies=_read_bodies(project_dir, rel),
        cards=format_cards(retrieve(instruction, k=3)))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": instruction}]

    files: dict[str, str] = {}
    log_tail = ""
    jar = None
    sent = set(rel)

    for attempt in range(1, max_fix_rounds + 2):
        if usage.prompt_tokens + usage.completion_tokens > max_tokens_run:
            log_tail = f"token budget exceeded ({max_tokens_run}) — stopping"
            emit(log_tail)
            break
        emit(f"edit attempt {attempt}: calling the model...")
        resp = client.chat.completions.create(
            model=mdl, temperature=0.3, max_tokens=8192, messages=messages)
        usage.add(resp)
        text = resp.choices[0].message.content or ""
        parsed = _parse_files(text)
        if not parsed:
            log_tail = "model produced no parseable files"
            emit(log_tail)
            break
        files.update(parsed)
        _merge_files(project_dir, parsed)
        emit(f"edit attempt {attempt}: {len(parsed)} file(s) changed -> compiling...")
        ok, log_tail, jar = _compile(project_dir, maven_bin)
        if not ok and _is_infra_error(log_tail):
            _purge_corrupt_artifacts(log_tail)
            ok, log_tail, jar = _compile(project_dir, maven_bin)
        if ok:
            emit(f"compiled OK -> {jar}")
            build_index(project_dir, index.get("request", ""), mc_version)   # symbols moved
            break
        if _is_infra_error(log_tail):
            emit("compile blocked by an infrastructure error")
            break
        # if the errors mention files the model hasn't seen, hand them over
        extra = ""
        error_files = {p.replace("\\", "/") for p in _ERRFILE_RE.findall(log_tail)} - sent
        if error_files:
            extra = "\nCurrent content of files referenced by the errors:\n" + \
                _read_bodies(project_dir, sorted(error_files)[:3])
            sent |= error_files
            emit(f"adding {len(error_files)} error-referenced file(s) to context")
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user",
                         "content": EDIT_FIX_PROMPT.format(errors=log_tail[:6000], extra_context=extra)})

    result = ForgeResult(
        ok=jar is not None,
        project_dir=os.path.abspath(project_dir),
        jar_path=os.path.abspath(jar) if jar else None,
        files=sorted(files),
        attempts=usage.calls,
        usage=usage,
        duration_s=round(time.time() - t0, 1),
        error=None if jar else log_tail,
        compile_log_tail=log_tail,
        events=events,
    )
    _log_run(instruction, mc_version, mdl, result, kind="edit")
    return result


# --- run ledger: the margin meter -----------------------------------------

_LEDGER = os.environ.get("FORGE_LEDGER",
                         os.path.join(os.path.dirname(__file__), "runs.jsonl"))


def _log_run(request: str, mc_version: str, model: str, r: ForgeResult, kind: str = "forge") -> None:
    """Append every run to the ledger — success rate, tokens and cost over time
    are what decide the hosted price point (and private-vs-OS)."""
    import json
    rec = {
        "ts": int(time.time()),
        "kind": kind,
        "request": request[:200],
        "mc": mc_version,
        "model": model,
        "ok": r.ok,
        "files": len(r.files),
        "llm_calls": r.usage.calls,
        "prompt_tokens": r.usage.prompt_tokens,
        "completion_tokens": r.usage.completion_tokens,
        "cost_usd": (round(r.usage.cost_usd, 6)
                     if r.usage.cost_usd is not None else None),
        "duration_s": r.duration_s,
        "error": (r.error or "")[:300] or None,
    }
    try:
        with open(_LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass                                            # metering must never break a forge

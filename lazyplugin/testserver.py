"""Spin up a throwaway Paper server and load the plugin into it.

A compiled jar is not proof of anything. This closes the loop: download a
Paper server, drop the plugin in, boot it, and read the log. If the plugin
throws on enable you find out in twenty seconds instead of on your live server.

The EULA is never accepted for you. Minecraft requires agreeing to it, and
that has to be your act, so the caller must pass accept_eula=True.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request

API = "https://fill.papermc.io/v3/projects/paper"
EULA_URL = "https://aka.ms/MinecraftEULA"
UA = {"User-Agent": "lazyplugin (+https://github.com/neuregex/lazyplugin)"}


def _get(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_version(version: str) -> str:
    """Turn a family like 1.21 into the newest stable release in it.

    PaperMC groups releases under a minor key, so "1.21" is a family whose
    newest member is something like 1.21.11. An exact version is returned
    unchanged, and pre-releases are skipped unless you name one explicitly.
    """
    data = _get(API)
    families: dict[str, list[str]] = data.get("versions", {})
    for members in families.values():
        if version in members:
            return version
    if version in families:
        stable = [v for v in families[version]
                  if not any(t in v for t in ("-rc", "-pre"))]
        if stable:
            return stable[0]
        return families[version][0]
    known = ", ".join(list(families)[:8])
    raise RuntimeError(f"Paper has no version {version!r}. Known families: {known}")


def latest_build(version: str) -> tuple[int, str, str]:
    """(build number, jar filename, download url) for the newest build."""
    builds = _get(f"{API}/versions/{version}/builds")
    if not builds:
        raise RuntimeError(f"no Paper builds published for {version}")
    stable = next((b for b in builds if b.get("channel") == "STABLE"), builds[0])
    dl = stable["downloads"]
    entry = dl.get("server:default") or next(iter(dl.values()))
    return stable["id"], entry["name"], entry["url"]


def java_version() -> int | None:
    """Major version of the java on PATH, or None if there is none."""
    exe = shutil.which("java")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=20)
    except Exception:                                          # noqa: BLE001
        return None
    blob = (out.stderr or "") + (out.stdout or "")
    for token in blob.replace('"', " ").split():
        head = token.split(".")[0]
        if head.isdigit():
            major = int(head)
            # "1.8.0_401" style: the real major is the second component
            return 8 if major == 1 else major
    return None


def prepare(server_dir: str, version: str = "1.21", plugin_jar: str | None = None,
            accept_eula: bool = False, port: int = 25565,
            on_event=None) -> str:
    """Create (or refresh) a server directory and return the server jar path."""
    def emit(msg: str):
        if on_event:
            on_event(msg)

    if not accept_eula:
        raise RuntimeError(
            f"Running a Minecraft server means agreeing to the Minecraft EULA "
            f"({EULA_URL}). Re-run with --accept-eula once you have read it.")

    jv = java_version()
    if jv is None:
        raise RuntimeError("No java found on PATH. Paper needs a JDK, 21 or newer.")
    if jv < 21:
        emit(f"warning: java {jv} found; recent Paper builds need java 21+")

    os.makedirs(server_dir, exist_ok=True)
    resolved = resolve_version(version)
    build, name, url = latest_build(resolved)
    emit(f"Paper {resolved} build {build}")

    jar = os.path.join(server_dir, name)
    if os.path.exists(jar):
        emit(f"already downloaded: {name}")
    else:
        emit(f"downloading {name} ...")
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=180) as r, open(jar, "wb") as f:
            shutil.copyfileobj(r, f)
        emit(f"downloaded {os.path.getsize(jar) // (1024 * 1024)} MB")

    with open(os.path.join(server_dir, "eula.txt"), "w", encoding="utf-8") as f:
        f.write(f"# accepted by the user via lazyplugin ({EULA_URL})\neula=true\n")

    props = os.path.join(server_dir, "server.properties")
    if not os.path.exists(props):
        with open(props, "w", encoding="utf-8") as f:
            # A scratch server for testing one plugin: no auth (so any client
            # can join), no spawn protection, and a flat world that loads fast.
            # Fine on loopback, not something to expose to the internet.
            f.write("\n".join([
                f"server-port={port}",
                "online-mode=false",
                "spawn-protection=0",
                "level-type=minecraft:flat",
                "max-players=5",
                "view-distance=6",
                "motd=LazyPlugin test server",
                "",
            ]))

    if plugin_jar:
        plugins = os.path.join(server_dir, "plugins")
        os.makedirs(plugins, exist_ok=True)
        dest = os.path.join(plugins, os.path.basename(plugin_jar))
        shutil.copyfile(plugin_jar, dest)
        emit(f"installed {os.path.basename(plugin_jar)} into plugins/")

    return jar


def start(server_dir: str, server_jar: str, memory: str = "2G",
          on_line=None, timeout_s: int = 240):
    """Boot the server, streaming console lines to on_line.

    Returns the Popen so a caller can keep it running (the web interface does)
    or stop it. Yielding through a callback rather than returning the whole log
    means you see the plugin load, or fail to, as it happens.
    """
    cmd = [shutil.which("java") or "java", f"-Xms{memory}", f"-Xmx{memory}",
           "-jar", os.path.basename(server_jar), "--nogui"]
    proc = subprocess.Popen(cmd, cwd=server_dir, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
    if on_line:
        for line in proc.stdout:                               # type: ignore[union-attr]
            on_line(line.rstrip())
            if proc.poll() is not None:
                break
    return proc


def stop(proc) -> None:
    """Ask the server to save and quit, then insist if it does not."""
    try:
        if proc.poll() is None:
            proc.stdin.write("stop\n")
            proc.stdin.flush()
            proc.wait(timeout=30)
    except Exception:                                          # noqa: BLE001
        try:
            proc.kill()
        except Exception:                                      # noqa: BLE001
            pass

"""Command line: describe a plugin, get a jar.

    lazyplugin "give everyone a diamond every 5 minutes"
    lazyplugin --provider ollama --model qwen2.5-coder:32b "a /warp command"
    lazyplugin models --check
    lazyplugin serve
"""
from __future__ import annotations

import argparse
import sys

SUBCOMMANDS = {"new", "edit", "models", "serve"}


def _print_models(check: bool) -> int:
    from .models import MODELS, PRICES_AS_OF, PROVIDERS

    if check:
        from .models import check as probe
        print("Probing every provider that looks configured...\n")
        print(f"  {'provider':12} {'status':28} models")
        print("  " + "-" * 52)
        usable = 0
        for row in probe():
            mark = "+" if row["status"].startswith("ok") else " "
            if row["status"].startswith("ok"):
                usable += 1
            count = row["models"] or ""
            print(f"{mark} {row['provider']:12} {row['status']:28} {count}")
        print(f"\n  {usable} provider(s) ready to use.")
        if not usable:
            print("  Set an API key, or run a local model:  ollama serve")
        return 0

    by_provider: dict[str, list] = {}
    for m in MODELS:
        by_provider.setdefault(m.provider, []).append(m)

    print("Any OpenAI-compatible model works. These are just a starting point:\n")
    for pid, prov in PROVIDERS.items():
        entries = by_provider.get(pid, [])
        where = "local, free" if prov.local else (prov.env_key or "")
        ready = "ready" if prov.configured else "not configured"
        print(f"  {prov.label}  [{where}]  ({ready})")
        for m in entries:
            price = ""
            if m.price_in is not None and m.price_out is not None:
                price = ("free" if m.price_in == 0
                         else f"${m.price_in}/${m.price_out} per 1M")
            note = f"  {m.notes}" if m.notes else ""
            ref = f"{pid}/{m.id}"
            print(f"      {ref:44} {price}{note}")
        if not entries:
            print(f"      (no curated entries: pass any model id with --provider {pid})")
        print()
    print(f"Prices indicative as of {PRICES_AS_OF}. Check availability with:"
          f"  lazyplugin models --check")
    return 0


def _report(res, label: str) -> int:
    print()
    print(f"== {label} ==")
    print(f"  result     : {'OK - jar built' if res.ok else 'FAILED'}")
    print(f"  project    : {res.project_dir}")
    if res.jar_path:
        print(f"  jar        : {res.jar_path}")
    print(f"  files      : {len(res.files)} ({', '.join(res.files)})")
    print(f"  llm calls  : {res.usage.calls}")
    print(f"  tokens     : {res.usage.prompt_tokens} in / {res.usage.completion_tokens} out")
    cost = res.usage.cost_usd
    print(f"  est. cost  : " + (f"${cost:.4f}" if cost is not None
                                else "unknown (no published price for this model)"))
    print(f"  duration   : {res.duration_s}s")
    if not res.ok:
        print(f"  error tail :\n{res.compile_log_tail[:1500]}")
    return 0 if res.ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `lazyplugin "some request"` stays valid: anything that is not a known
    # subcommand is treated as a build request.
    if argv and argv[0] not in SUBCOMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "new")

    ap = argparse.ArgumentParser(
        prog="lazyplugin",
        description="Describe a Minecraft server plugin, get a compiled Paper jar.")
    sub = ap.add_subparsers(dest="cmd")

    def add_model_flags(p):
        p.add_argument("--model", default=None,
                       help="model id, or provider/model (e.g. openai/gpt-4o)")
        p.add_argument("--provider", default=None,
                       help="deepseek, openai, anthropic, ollama, ... (see: lazyplugin models)")
        p.add_argument("--key", default=None, help="API key (default: provider env var)")
        p.add_argument("--base-url", default=None, help="any OpenAI-compatible endpoint")
        p.add_argument("--fix-rounds", type=int, default=2, help="max compile-fix retries")

    pn = sub.add_parser("new", help="create a plugin")
    pn.add_argument("request", help="what the plugin should do")
    pn.add_argument("--version", default="1.21", help="target Minecraft version")
    pn.add_argument("--out", default=None, help="project output directory")
    pn.add_argument("-k", type=int, default=6, help="recipe cards to retrieve")
    add_model_flags(pn)

    pe = sub.add_parser("edit", help="change an existing project")
    pe.add_argument("project_dir")
    pe.add_argument("instruction")
    add_model_flags(pe)

    pm = sub.add_parser("models", help="list models and check what is reachable")
    pm.add_argument("--check", action="store_true", help="probe configured providers")

    ps = sub.add_parser("serve", help="open the local web interface")
    ps.add_argument("--port", type=int, default=8321)
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--no-browser", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_help()
        return 2

    if args.cmd == "models":
        return _print_models(args.check)

    if args.cmd == "serve":
        from .web.app import serve
        return serve(host=args.host, port=args.port, open_browser=not args.no_browser)

    from .core import forge, forge_edit
    progress = lambda m: print("  * " + m, flush=True)   # noqa: E731
    try:
        if args.cmd == "edit":
            res = forge_edit(args.project_dir, args.instruction,
                             api_key=args.key, base_url=args.base_url,
                             model=args.model, provider=args.provider,
                             max_fix_rounds=args.fix_rounds, on_event=progress)
            return _report(res, "LazyPlugin edit report")
        res = forge(args.request, mc_version=args.version, out_dir=args.out,
                    api_key=args.key, base_url=args.base_url,
                    model=args.model, provider=args.provider,
                    max_fix_rounds=args.fix_rounds, k=args.k, on_event=progress)
        return _report(res, "LazyPlugin report")
    except (RuntimeError, ValueError) as exc:
        # Missing key, unknown provider, unsupported version: the message
        # already says what to do, so do not bury it in a traceback.
        print(f"\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

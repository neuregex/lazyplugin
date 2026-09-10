"""The margin meter: aggregate the run ledger into the numbers that price the
hosted tier (and settle private-vs-OS).

    python -m lazyplugin.stats

Assumptions are explicit + env-tunable:
    FORGE_CREDIT_USD   effective revenue per credit   (default 0.030 — Pro-tier ballpark)
    FORGE_CREDITS      credits charged per forge      (default 5)
"""
from __future__ import annotations

import json
import os
import statistics

from .core import _LEDGER

CREDIT_USD = float(os.environ.get("FORGE_CREDIT_USD", "0.030"))
CREDITS = int(os.environ.get("FORGE_CREDITS", "5"))


def load_runs() -> list[dict]:
    if not os.path.exists(_LEDGER):
        return []
    runs = []
    with open(_LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return runs


def main() -> int:
    runs = load_runs()
    if not runs:
        print("No runs in the ledger yet:", _LEDGER)
        return 1
    ok = [r for r in runs if r["ok"]]
    fail = [r for r in runs if not r["ok"]]
    first_shot = [r for r in ok if r["llm_calls"] == 1]
    fixed = [r for r in ok if r["llm_calls"] > 1]
    costs = [r["cost_usd"] or 0 for r in ok]
    durs = [r["duration_s"] for r in ok]

    def pct(part, whole):
        return f"{100 * len(part) / max(len(whole), 1):.0f}%"

    print("== LazyPlugin — margin meter ==")
    print(f"  runs               : {len(runs)}  (ledger: {_LEDGER})")
    for kind in ("forge", "edit"):
        sub = [r for r in ok if r.get("kind", "forge") == kind]
        if sub:
            c = [r["cost_usd"] or 0 for r in sub]
            print(f"    {kind:<5}            : {len(sub)} ok | avg ${statistics.mean(c):.4f}"
                  f" | max ${max(c):.4f}")
    print(f"  success            : {len(ok)}/{len(runs)}  ({pct(ok, runs)})")
    print(f"  first-shot compile : {len(first_shot)}/{len(ok)}  ({pct(first_shot, ok)} of successes)")
    print(f"  fixed-by-loop      : {len(fixed)}/{len(ok)}  ({pct(fixed, ok)} of successes)")
    if costs:
        print(f"  cost per plugin    : avg ${statistics.mean(costs):.4f}"
              f" | median ${statistics.median(costs):.4f}"
              f" | max ${max(costs):.4f}")
        print(f"  duration           : avg {statistics.mean(durs):.0f}s | max {max(durs):.0f}s")
        total_cost = sum(r["cost_usd"] or 0 for r in runs)
        print(f"  total spend        : ${total_cost:.4f} over {len(runs)} runs"
              f" (incl. failures: ${sum(r['cost_usd'] or 0 for r in fail):.4f})")
        price = CREDITS * CREDIT_USD
        avg = statistics.mean(costs)
        print(f"  -- margin model (assumes {CREDITS} credits/forge at ${CREDIT_USD:.3f}/credit = ${price:.2f}) --")
        print(f"  gross margin/forge : ${price - avg:.4f}  ({100 * (price - avg) / price:.1f}%)")
        worst = max(costs)
        print(f"  worst-case margin  : ${price - worst:.4f}  ({100 * (price - worst) / price:.1f}%)")
    if fail:
        print("  failures:")
        for r in fail:
            print(f"    - [{r['llm_calls']} calls, ${r['cost_usd'] or 0:.4f}] {r['request'][:80]}")

    # -- pricing-model simulation: FLAT credits vs TOKEN-LINKED credits --------
    # Token model: 1 credit = TOKENS_PER_CREDIT tokens (in+out), rounded up,
    # with a minimum. Answers "¿no es mejor por tokens?" with the real ledger.
    if ok:
        tokens_per_credit = int(os.environ.get("FORGE_TOKENS_PER_CREDIT", "5000"))
        min_credits = 2
        tok_credits = []
        for r in ok:
            t = r["prompt_tokens"] + r["completion_tokens"]
            tok_credits.append(max(min_credits, -(-t // tokens_per_credit)))
        flat_rev = len(ok) * CREDITS * CREDIT_USD
        tok_rev = sum(c * CREDIT_USD for c in tok_credits)
        cost = sum(costs)
        print(f"  -- pricing simulation over {len(ok)} successful forges --")
        print(f"  FLAT  {CREDITS} cr/forge      : revenue ${flat_rev:.2f} | margin {100 * (flat_rev - cost) / flat_rev:.1f}%"
              f" | user always knows the price")
        print(f"  TOKEN 1cr={tokens_per_credit} tok : revenue ${tok_rev:.2f} | margin {100 * (tok_rev - cost) / tok_rev:.1f}%"
              f" | credits/forge ranged {min(tok_credits)}-{max(tok_credits)}"
              f" (avg {statistics.mean(tok_credits):.1f})")
        print(f"  note: {pct(fixed, ok)} of successes needed OUR fix rounds — under the"
              f" token model the user pays more when the model fails more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Refresh list prices in infra/pricing/model_prices.json and push them to the deployed spend alerts and workbook.

  python refresh_prices.py            # refresh list prices + sync Azure
  python refresh_prices.py --dry-run  # show changes only
  python refresh_prices.py --no-sync  # update the files only

Models with "source": "manual" (negotiated prices) are never overwritten. Run weekly:
scripts/refresh-prices.ps1 -Register creates a Windows scheduled task (needs a valid `az login`).
"""
from __future__ import annotations

import argparse
import copy

import common

common.load_env()

import cost  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-sync", action="store_true")
    args = ap.parse_args()

    before = cost.load_prices()
    after, notes = cost.refresh_list_prices(copy.deepcopy(before), before.get("region") or common.env("AZURE_LOCATION"))
    changes = []
    for model, e in after["models"].items():
        for k in cost.TOKEN_TYPES:
            old, new = before["models"].get(model, {}).get(k), e.get(k)
            if old != new:
                changes.append(f"{model} {k}: {old} -> {new}")
    print("\n".join(changes) if changes else "No price changes.")
    for n in notes:
        print(f"note: {n}")
    if args.dry_run:
        return 0
    cost.save_prices(after)
    if not args.no_sync:
        synced = cost.sync_azure(after)
        print("Synced: " + (", ".join(synced) if synced else "nothing to update"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

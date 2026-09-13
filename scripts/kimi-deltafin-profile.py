#!/usr/bin/env python3
"""Print a reviewable Deltafin Kimi multi-drive candidate.

This is deliberately read-only: it never copies, deletes, or launches an
engine. Replica arguments are positional: DIR_B, HOT_DIR, DIR_C.
"""
from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "monitor"))
from model_support import deltafin_kimi_profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_root", help="absolute Deltafin model root")
    parser.add_argument("replicas", nargs="*", help="DIR_B, HOT_DIR, and optional DIR_C")
    parser.add_argument("--shell", action="store_true", help="print export assignments instead of JSON")
    args = parser.parse_args()
    profile = deltafin_kimi_profile(args.model_root, args.replicas)
    checks = []
    for role, raw in [("DELTAFIN_ROOT", args.model_root),
                      *[(f"replica_{i+1}", p) for i, p in enumerate(args.replicas)]]:
        path = Path(raw)
        item = {"role": role, "path": str(path), "exists": path.exists(), "directory": path.is_dir()}
        try:
            st = path.stat()
            item["filesystem_device"] = st.st_dev
            item["regular_file"] = stat.S_ISREG(st.st_mode)
        except OSError as exc:
            item["error"] = str(exc)
            profile["errors"].append(f"{role} is not accessible: {path}")
        if item.get("exists") and role != "DELTAFIN_ROOT" and not item.get("directory"):
            profile["errors"].append(f"{role} must be a directory: {path}")
        checks.append(item)
    devices = [x.get("filesystem_device") for x in checks if x.get("directory")]
    if len(devices) != len(set(devices)):
        profile["errors"].append("At least two selected directories share one filesystem device; verify the topology.")
    profile["path_checks"] = checks
    profile["read_only"] = True
    if args.shell:
        for key, value in profile["environment"].items():
            print(f"export {key}={json.dumps(value)}")
        return 1 if profile["errors"] else 0
    print(json.dumps(profile, indent=2, sort_keys=True))
    return 1 if profile["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

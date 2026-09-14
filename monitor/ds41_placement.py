"""DeepSeek V4.1 usage-weighted placement and qualification tools.

This module ports the *decision* part of the Deltafin method to a monolithic
DeepSeek GGUF.  It never copies or edits model files.  The output is a signed
set of facts for an engine integration to consume:

* router trace -> per-layer/expert traffic;
* GGUF routed tensor directory -> absolute component spans;
* measured source rates -> a traffic-balanced home/replica recommendation;
* matched A/B records -> a qualification report.

The engine still needs an explicit opt-in to consume a generated manifest.
Generating one must not silently change the active serving configuration.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
from typing import Any, Iterable

from optimizer_storage import inspect_gguf


_KEY = re.compile(r"^[Ll](\d+)[-_][Ee](\d+)(?:\.bin)?$")
_TENSOR = re.compile(r"(?:^|/)blk\.(\d+)\.ffn_(gate|up|down)_exps\.weight$")
_DISK = re.compile(r"^disk\d+$")


def _positive_number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a positive number") from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be a positive number")
    return result


def _expert_key(layer: Any, expert: Any) -> str:
    try:
        layer, expert = int(layer), int(expert)
    except (TypeError, ValueError):
        raise ValueError("layer and expert IDs must be integers") from None
    if layer < 0 or expert < 0:
        raise ValueError("layer and expert IDs must be non-negative")
    return f"L{layer}-E{expert}"


def _ids(value: Any) -> list[int]:
    """Normalize a route record's ids/experts field."""
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = re.split(r"[ ,;]+", value)
    if not isinstance(value, (list, tuple)):
        raise ValueError("route ids must be a list or a delimited string")
    result = []
    for item in value:
        try:
            n = int(item)
        except (TypeError, ValueError):
            raise ValueError("route expert IDs must be integers") from None
        if n < 0:
            raise ValueError("route expert IDs must be non-negative")
        result.append(n)
    return result


def _add_route(counter: Counter, record: dict[str, Any], max_experts: int) -> tuple[int, int]:
    layer = record.get("layer")
    ids = record.get("ids", record.get("experts", record.get("expert_ids")))
    if layer is None or ids is None:
        raise ValueError("each route record needs layer and ids")
    ids = _ids(ids)
    if not ids or len(ids) > max_experts:
        raise ValueError(f"route record must contain 1–{max_experts} expert IDs")
    layer = int(layer)
    if layer < 0:
        raise ValueError("layer must be non-negative")
    for expert in ids:
        counter[_expert_key(layer, expert)] += 1
    return layer, int(record.get("step", 0) or 0)


def parse_router_trace(path: str | Path, max_records: int = 2_000_000,
                       max_experts: int = 256) -> dict[str, Any]:
    """Parse JSONL or CSV router traces into occurrence counts.

    JSONL accepts ``{layer, ids}`` (the format emitted by ds4's route dump),
    plus ``experts``/``expert_ids`` aliases. CSV accepts an ``ids`` column
    containing JSON/list text or columns named ``id0``, ``id1`` … .
    """
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("Router trace must be a regular file")
    counter: Counter[str] = Counter()
    records = 0
    steps: set[int] = set()
    layers: set[int] = set()
    with path.open(newline="", encoding="utf-8") as raw:
        first = raw.readline()
        raw.seek(0)
        if first.lstrip().startswith("{"):
            for line in raw:
                if not line.strip():
                    continue
                if records >= max_records:
                    raise ValueError("router trace exceeds record limit")
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL route record {records + 1}") from exc
                if not isinstance(obj, dict):
                    raise ValueError("route record must be an object")
                layer, step = _add_route(counter, obj, max_experts)
                records += 1; layers.add(layer); steps.add(step)
        else:
            reader = csv.DictReader(raw)
            if not reader.fieldnames or "layer" not in reader.fieldnames:
                raise ValueError("CSV router trace needs a layer column")
            id_columns = sorted(
                (name for name in reader.fieldnames if re.fullmatch(r"(?:id|expert)[_-]?\d+", name or "", re.I)),
                key=lambda name: int(re.search(r"\d+$", name).group()),
            )
            for row in reader:
                if records >= max_records:
                    raise ValueError("router trace exceeds record limit")
                if "ids" not in row and "experts" not in row and not id_columns:
                    raise ValueError("CSV router trace needs ids or id0/id1 columns")
                if "ids" in row and row.get("ids", "").strip():
                    values = row["ids"]
                elif "experts" in row and row.get("experts", "").strip():
                    values = row["experts"]
                else:
                    values = [row[name] for name in id_columns if row.get(name, "").strip()]
                layer, step = _add_route(counter, {"layer": row["layer"], "step": row.get("step", 0), "ids": values}, max_experts)
                records += 1; layers.add(layer); steps.add(step)
    if not records:
        raise ValueError("Router trace has no route records")
    return {
        "schema": 1, "source": str(path), "format": "jsonl" if first.lstrip().startswith("{") else "csv",
        "records": records, "steps": len(steps), "layers": sorted(layers),
        "occurrences": dict(sorted(counter.items())),
        "total_routes": sum(counter.values()),
    }


def load_usage_file(path: str | Path) -> dict[str, int]:
    """Load Deltafin-style ``Lx-Ey.bin: count`` JSON usage files."""
    try:
        obj = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Usage file must be valid JSON") from exc
    if not isinstance(obj, dict) or not obj:
        raise ValueError("Usage file must be a non-empty object")
    out: dict[str, int] = {}
    for raw_key, raw_count in obj.items():
        match = _KEY.fullmatch(str(raw_key))
        if not match:
            raise ValueError(f"invalid expert usage key: {raw_key}")
        count = int(raw_count)
        if count < 0:
            raise ValueError("expert usage counts must be non-negative")
        out[_expert_key(match.group(1), match.group(2))] = count
    return out


def expert_spans(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Expand routed GGUF tensors into one absolute span per layer/expert."""
    grouped: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for tensor in model.get("tensors", []):
        match = _TENSOR.search(str(tensor.get("name", "")))
        if not match:
            continue
        layer, component = int(match.group(1)), match.group(2)
        count = int(tensor["experts"]); size = int(tensor["component_bytes"])
        if count <= 0 or size <= 0:
            raise ValueError("GGUF expert tensor has invalid dimensions")
        for expert in range(count):
            key = _expert_key(layer, expert)
            row = grouped[layer].setdefault(key, {"layer": layer, "expert": expert, "components": {}})
            row["components"][component] = {"offset": int(tensor["offset"]) + expert * size, "bytes": size}
    out: dict[str, dict[str, Any]] = {}
    for layer in sorted(grouped):
        for key, row in sorted(grouped[layer].items()):
            if set(row["components"]) != {"gate", "up", "down"}:
                raise ValueError(f"incomplete routed expert span: {key}")
            row["bytes"] = sum(int(c["bytes"]) for c in row["components"].values())
            row["span_end"] = max(int(c["offset"]) + int(c["bytes"]) for c in row["components"].values())
            out[key] = row
    if not out:
        raise ValueError("No routed DeepSeek expert tensors found in GGUF")
    return out


def map_model_experts(model_path: str | Path) -> dict[str, Any]:
    model_path = Path(model_path).expanduser().resolve(strict=True)
    model = inspect_gguf(model_path)
    spans = expert_spans(model)
    return {"schema": 1, "model": {k: model[k] for k in ("path", "bytes", "header_sha256", "architecture", "name", "data_start", "component_sizes")},
            "experts": spans, "expert_count": len(spans),
            "routed_bytes": sum(v["bytes"] for v in spans.values())}


def usage_records(usage: dict[str, int], spans: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for key, span in spans.items():
        count = int(usage.get(key, 0))
        records.append({**span, "id": key, "uses": count, "traffic_bytes": count * int(span["bytes"])})
    return sorted(records, key=lambda r: (-r["traffic_bytes"], r["id"]))


def recommend_placement(spans: dict[str, dict[str, Any]], usage: dict[str, int],
                        rates: dict[str, float], links: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assign hot experts by largest target-traffic deficit.

    The target for each source is proportional to its *measured* application
    rate. This is the Deltafin rule adapted to barrier-bound expert reads.
    ``links`` is retained as evidence and is not silently summed: callers can
    provide effective shared-uplink rates after measuring them.
    """
    if not rates or len(rates) > 8:
        raise ValueError("Provide one to eight measured source rates")
    rates = {str(name): _positive_number(rate, f"rate for {name}") for name, rate in rates.items()}
    total_rate = sum(rates.values())
    records = usage_records(usage, spans)
    total_traffic = sum(r["traffic_bytes"] for r in records)
    targets = {name: total_traffic * rate / total_rate for name, rate in rates.items()}
    loads = {name: 0 for name in rates}
    placements = []
    order = list(rates)
    for record in records:
        name = max(order, key=lambda n: (targets[n] - loads[n], -order.index(n)))
        loads[name] += record["traffic_bytes"]
        placements.append({"id": record["id"], "home": name, "uses": record["uses"],
                           "traffic_bytes": record["traffic_bytes"], "spans": record["components"]})
    total = max(1, total_traffic)
    return {"schema": 1, "method": "Deltafin largest target-traffic deficit",
            "rates_gbps": rates, "targets_bytes": targets, "assigned_bytes": loads,
            "assigned_share": {n: loads[n] / total for n in order},
            "traffic_bytes": total_traffic, "expert_count": len(records),
            "links": links or {}, "placements": placements,
            "notes": ["Rates must be measured application/device windows; advertised link speeds are not substitutes.",
                      "Shared uplinks are one resource. Supply their measured effective rate instead of summing member rates.",
                      "This manifest is advisory until the engine explicitly consumes it; no model files were copied."]}


def _record_from_json(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser().resolve(strict=True)
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("benchmark record must be a JSON object")
    # Accept a ds41 run.json wrapper or a direct result record.
    result = obj.get("result") if isinstance(obj.get("result"), dict) else obj
    out = dict(result)
    out["_path"] = str(path)
    if obj.get("model_sha256") is not None:
        out.setdefault("model_sha256", obj.get("model_sha256"))
    # ds41 stores the immutable prompt/engine identities in plan.json and the
    # measured result in run.json. Join them here so a qualification command
    # can consume the native run directory without hand-editing records.
    if path.name == "run.json":
        plan_path = path.parent / "plan.json"
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if isinstance(plan, dict):
                out.setdefault("prompt_sha256", plan.get("prompt_sha256"))
                out.setdefault("engine_sha256", plan.get("engine_sha256"))
                out.setdefault("prompt_tokens", plan.get("prompt_tokens"))
                out.setdefault("generated_tokens", plan.get("generated_tokens"))
        except (OSError, json.JSONDecodeError):
            pass
    generated = out.get("generated.txt")
    if generated and "output_sha256" not in out:
        out["output_sha256"] = hashlib.sha256(Path(generated).read_bytes()).hexdigest()
    return out


def qualify_ab(baseline_path: str | Path, candidate_path: str | Path) -> dict[str, Any]:
    """Compare two matched records with strict identity/output gates."""
    a, b = _record_from_json(baseline_path), _record_from_json(candidate_path)
    identity_fields = ("prompt_tokens", "generated_tokens", "model_sha256", "prompt_sha256", "engine_sha256")
    mismatches = {field: [a.get(field), b.get(field)] for field in identity_fields
                  if a.get(field) is None or b.get(field) is None or a.get(field) != b.get(field)}
    output_equal = bool(a.get("output_sha256") and a.get("output_sha256") == b.get("output_sha256"))
    result = {"schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "baseline": str(a["_path"]), "candidate": str(b["_path"]),
              "identity_mismatches": mismatches, "output_equal": output_equal,
              "qualified": not mismatches and output_equal, "gains_percent": {}}
    for key in ("generation_tok_s", "steady_tok_s", "prefill_tok_s"):
        av, bv = a.get(key), b.get(key)
        if isinstance(av, (int, float)) and isinstance(bv, (int, float)) and av > 0:
            result["gains_percent"][key] = 100 * (bv / av - 1)
    if not result["qualified"]:
        result["reasons"] = ([f"identity mismatch: {k}" for k in mismatches] +
                              ([] if output_equal else ["output SHA-256 differs or is absent"]))
    return result


def _parse_named(values: Iterable[str], label: str) -> dict[str, str]:
    out = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"{label} must use NAME=VALUE")
        name, value = item.split("=", 1)
        if not name or not value or name in out:
            raise ValueError(f"invalid or duplicate {label}: {item}")
        out[name] = value
    return out


def build_manifest(model_path: str | Path, trace_path: str | Path | None,
                   usage_path: str | Path | None, rates: dict[str, float], out: Path,
                   links: dict[str, Any] | None = None) -> dict[str, Any]:
    mapped = map_model_experts(model_path)
    usage = load_usage_file(usage_path) if usage_path else parse_router_trace(trace_path)["occurrences"]
    placement = recommend_placement(mapped["experts"], usage, rates, links)
    report = {"schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "model": mapped["model"], "trace": str(Path(trace_path).resolve()) if trace_path else None,
              "usage_file": str(Path(usage_path).resolve()) if usage_path else None,
              "usage_records": len(usage), "expert_map": mapped, "placement": placement}
    out.mkdir(parents=True, exist_ok=True)
    (out / "ds41-expert-spans.json").write_text(json.dumps(mapped, indent=2) + "\n")
    (out / "ds41-placement.json").write_text(json.dumps(report, indent=2) + "\n")
    for source in rates:
        rows = [p for p in placement["placements"] if p["home"] == source]
        (out / f"manifest-{source}.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    route = sub.add_parser("route", help="parse a JSONL/CSV router trace")
    route.add_argument("--trace", type=Path, required=True); route.add_argument("--out", type=Path, required=True)
    spans = sub.add_parser("spans", help="map DeepSeek routed experts to GGUF spans")
    spans.add_argument("--model", type=Path, required=True); spans.add_argument("--out", type=Path, required=True)
    build = sub.add_parser("build", help="build a usage-weighted placement manifest")
    build.add_argument("--model", type=Path, required=True); build.add_argument("--trace", type=Path)
    build.add_argument("--usage", type=Path); build.add_argument("--rate", action="append", default=[], metavar="NAME=GBPS")
    build.add_argument("--out", type=Path, required=True)
    qualify = sub.add_parser("qualify", help="strictly compare matched A/B JSON records")
    qualify.add_argument("--baseline", type=Path, required=True); qualify.add_argument("--candidate", type=Path, required=True); qualify.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "route": result = parse_router_trace(args.trace); args.out.write_text(json.dumps(result, indent=2) + "\n")
        elif args.action == "spans": result = map_model_experts(args.model); args.out.write_text(json.dumps(result, indent=2) + "\n")
        elif args.action == "build":
            if bool(args.trace) == bool(args.usage): raise ValueError("provide exactly one of --trace or --usage")
            rates = {k: _positive_number(v, f"rate for {k}") for k, v in _parse_named(args.rate, "--rate").items()}
            if not rates: raise ValueError("provide at least one measured --rate NAME=GBPS")
            result = build_manifest(args.model, args.trace, args.usage, rates, args.out)
        else:
            result = qualify_ab(args.baseline, args.candidate); args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

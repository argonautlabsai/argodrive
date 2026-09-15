"""Read what the engine actually did, not what it was asked to do.

Two instruments, both born from 2026-09-15:

``prefill_path`` answers "did the prefill optimisation engage?" from the engine
log. For weeks a single-drive run silently fell back to the layer-major sweep;
the only evidence was one line, ``prefill stage rejected: reader invalid=0
sources=1``. Every run card should carry that answer.

``dead_knobs`` checks each DS4_* variable a profile sets against the strings in
the engine binary. Twelve of the twenty-one Argodrive knobs the monitor set did
not exist in the engine; four of them appeared in a flag sweep reported as
"neutral" -- those arms measured no-ops.
"""
import re
from functools import lru_cache
from pathlib import Path

_SOURCES = re.compile(rb'^ds4: experimental Argodrive expert reader sources=(\d+)', re.M)
_SELECTIVE = re.compile(rb'^ds4: Argodrive selective prefill staging for chunks <= (\d+) tokens', re.M)
_PIPELINED = re.compile(rb'^ds4: Argodrive pipelined prefill staging in (\d+)-token chunks', re.M)
_LANES = re.compile(rb'^ds4: Argodrive prefill staging lanes=(\d+)', re.M)
_REJECTED = re.compile(rb'^ds4: Argodrive prefill stage rejected: (.+)$', re.M)
_FAILED = re.compile(rb'^ds4: V4\.1 layer-major prefill failed at layer (\d+)', re.M)
_STAGED = re.compile(
    rb'^ds4: Argodrive prefill staged\((selected|all)\) experts=(\d+) bytes=(\d+) of (\d+) \((\d+)%\)', re.M)
_ARGODRIVE_ANY = re.compile(rb'^ds4: (?:experimental )?Argodrive ', re.M)


def prefill_path(raw):
    """Classify the prefill path an engine run took.

    Returns a dict with ``path`` in {"staged-selective", "staged-full",
    "sweep-after-rejection", "sweep"} plus the evidence behind it. ``raw`` is
    the engine log as bytes (``baseline.engine.txt``).
    """
    if isinstance(raw, str):
        raw = raw.encode('utf-8', 'replace')
    sources = _SOURCES.search(raw)
    selective = _SELECTIVE.search(raw)
    pipelined = _PIPELINED.search(raw)
    lanes = _LANES.search(raw)
    rejections = [m.group(1).decode('utf-8', 'replace').strip() for m in _REJECTED.finditer(raw)]
    failed = _FAILED.search(raw)
    staged = _STAGED.findall(raw)

    read_bytes = sum(int(b) for _, _, b, _, _ in staged)
    full_bytes = sum(int(f) for _, _, _, f, _ in staged)
    experts = sum(int(e) for _, e, _, _, _ in staged)
    selected_layers = sum(1 for kind, *_ in staged if kind == b'selected')

    if staged:
        path = 'staged-selective' if selected_layers else 'staged-full'
    elif rejections or failed:
        path = 'sweep-after-rejection'
    else:
        path = 'sweep'

    out = {
        'path': path,
        'argodrive_present': bool(_ARGODRIVE_ANY.search(raw)),
        'sources': int(sources.group(1)) if sources else None,
        'selective_requested': bool(selective),
        'selective_max_tokens': int(selective.group(1)) if selective else None,
        'pipelined_chunk_tokens': int(pipelined.group(1)) if pipelined else None,
        'lanes': int(lanes.group(1)) if lanes else None,
        'staged_layers': len(staged),
        'staged_experts': experts,
        'staged_bytes': read_bytes,
        'full_layer_bytes': full_bytes,
        'coverage': (read_bytes / full_bytes) if full_bytes else None,
        'rejections': rejections,
        'failed_at_layer': int(failed.group(1)) if failed else None,
    }
    out['engaged'] = path.startswith('staged')
    out['verdict'] = _verdict(out)
    return out


def _verdict(o):
    if o['path'] == 'staged-selective':
        cov = o['coverage']
        return ('selective prefill engaged on %s source(s); read %.0f%% of the full layer bytes'
                % (o['sources'], 100 * cov)) if cov is not None else 'selective prefill engaged'
    if o['path'] == 'staged-full':
        return 'staged prefill engaged (full layers, no selection) on %s source(s)' % o['sources']
    if o['path'] == 'sweep-after-rejection':
        why = o['rejections'][0] if o['rejections'] else 'layer-major prefill failed'
        return 'prefill staging REJECTED (%s); fell back to the layer-major sweep' % why
    if o['argodrive_present']:
        return 'no prefill staging requested; layer-major sweep read every expert'
    return 'upstream engine; layer-major sweep read every expert'


@lru_cache(maxsize=16)
def _binary_bytes(binary):
    """The engine binary's bytes, or b'' if unreadable. No subprocess: a knob
    name the engine reads with getenv() is a literal in the binary."""
    p = Path(binary)
    try:
        return p.read_bytes() if p.is_file() else b''
    except OSError:
        return b''


def _present(blob, name):
    # NUL-terminated so DS4_ARGODRIVE_PREFILL does not match _PREFILL_LANES.
    return (name.encode() + b'\0') in blob


def dead_knobs(binary, env, prefixes=('DS4_',)):
    """Return the environment variables in ``env`` the engine binary never reads.

    A knob whose name is not a literal in the binary cannot be read by it, so
    setting it measures nothing. Only names with one of ``prefixes`` are
    checked; everything else is assumed to belong to the harness. An
    unreadable binary yields [] rather than declaring everything dead.
    """
    blob = _binary_bytes(binary)
    if not blob:
        return []
    return sorted(k for k in env if k.startswith(prefixes) and not _present(blob, k))


def live_knobs(binary, env, prefixes=('DS4_',)):
    blob = _binary_bytes(binary)
    return sorted(k for k in env if k.startswith(prefixes) and _present(blob, k))

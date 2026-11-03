"""Bill neighborhoods for the network map, with the layout computed at build time (N3).

A neighborhood is the bill, every bill linked to it, and one more step out (N1),
capped so the picture stays readable. Layout is a small deterministic force-directed
pass in numpy, so the same graph always draws the same way and the browser only draws.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

import numpy as np

MAX_NODES = 260
MAX_FRONTIER = 120
W, H = 900, 560


def adjacency(edges) -> dict[str, dict[str, str]]:
    """edges: iterable of (bill_id, related_id, relationship). Returns undirected adjacency
    with the strongest kind per pair ('identical' beats 'related')."""
    adj: dict[str, dict[str, str]] = defaultdict(dict)
    for a, b, rel in edges:
        if a == b:
            continue
        k = "identical" if rel == "Identical bill" else "related"
        for x, y in ((a, b), (b, a)):
            if adj[x].get(y) != "identical":
                adj[x][y] = k
    return adj


def neighborhood(adj, center: str) -> tuple[dict[str, int], list[tuple[str, str, str]]]:
    nodes = {center: 0}
    frontier = [center]
    for depth in (1, 2):
        nxt = []
        for n in frontier:
            for m in sorted(adj.get(n, ())):
                if m not in nodes and len(nodes) < MAX_NODES:
                    nodes[m] = depth
                    nxt.append(m)
        frontier = nxt[:MAX_FRONTIER]
    edges = set()
    for a in nodes:
        for b, k in adj.get(a, {}).items():
            if b in nodes and a < b:
                edges.add((a, b, k))
    return nodes, sorted(edges)


def layout(ids: list[str], edges: list[tuple[str, str, str]], radius: list[float], iters: int = 300, seed_key: str = "") -> np.ndarray:
    """Force-directed layout (springs on edges, repulsion between all pairs, mild gravity),
    scaled to fit a W x H box. Deterministic for a given input."""
    n = len(ids)
    if n <= 3:  # nothing to simulate: center, then one or two neighbours on either side
        return np.array([[W / 2, H / 2], [W / 2 - 160, H / 2], [W / 2 + 160, H / 2]][:n])
    idx = {k: i for i, k in enumerate(ids)}
    # Phyllotaxis start, like d3-force, so results do not depend on a random seed.
    i = np.arange(n)
    r0 = 10 * np.sqrt(0.5 + i)
    ang = i * np.pi * (3 - np.sqrt(5))
    pos = np.stack([r0 * np.cos(ang), r0 * np.sin(ang)], 1)
    if seed_key:  # tiny deterministic jitter so symmetric graphs do not stay collinear
        h = int(hashlib.md5(seed_key.encode()).hexdigest()[:8], 16)
        pos += (np.random.default_rng(h).random((n, 2)) - 0.5) * 1e-3
    vel = np.zeros_like(pos)
    rad = np.asarray(radius, float)
    minsep = rad[:, None] + rad[None, :] + 3
    if edges:
        ea = np.array([idx[a] for a, _, _ in edges])
        eb = np.array([idx[b] for _, b, _ in edges])
        elen = np.array([26.0 if k == "identical" else 58.0 for _, _, k in edges])
        deg = np.bincount(np.concatenate([ea, eb]), minlength=n).astype(float)
        bias = deg[ea] / np.maximum(deg[ea] + deg[eb], 1)
        strength = 0.6 / np.maximum(np.minimum(deg[ea], deg[eb]), 1)
    alpha = 1.0
    decay = 1 - 0.001 ** (1 / iters)
    for _ in range(iters):
        force = np.zeros_like(pos)
        # links (d3.forceLink semantics)
        if edges:
            d = (pos[eb] + vel[eb]) - (pos[ea] + vel[ea])
            dist = np.sqrt((d ** 2).sum(1)) + 1e-9
            f = ((dist - elen) / dist * alpha * strength)[:, None] * d
            np.add.at(force, eb, -f * bias[:, None])
            np.add.at(force, ea, f * (1 - bias)[:, None])
        # many-body repulsion and collision (exact; n <= MAX_NODES)
        dx = pos[None, :, 0] - pos[:, None, 0]
        dy = pos[None, :, 1] - pos[:, None, 1]
        d2 = dx * dx + dy * dy
        np.fill_diagonal(d2, np.inf)
        np.maximum(d2, 1.0, out=d2)
        dist = np.sqrt(d2)
        coef = -70.0 * alpha / d2
        overlap = minsep - dist
        with np.errstate(invalid="ignore"):
            coef -= np.where(overlap > 0, overlap / dist * 0.5, 0.0)
        force[:, 0] += (coef * dx).sum(1)
        force[:, 1] += (coef * dy).sum(1)
        # gravity toward the center (keeps isolated nodes on the page)
        force -= pos * 0.02 * alpha
        vel = (vel + force) * 0.6
        pos = pos + vel
        alpha += (0.001 - alpha) * decay
    pad = 24
    lo, hi = pos.min(0), pos.max(0)
    span = np.maximum(hi - lo, 1e-6)
    sc = min((W - 2 * pad) / span[0], (H - 2 * pad) / span[1], 1.6)
    mid = (lo + hi) / 2
    return np.stack([W / 2 + (pos[:, 0] - mid[0]) * sc, H / 2 + (pos[:, 1] - mid[1]) * sc], 1)


def node_radius(cos: int | None, center: bool) -> float:
    return 14.0 if center else 4 + np.sqrt(cos or 0) * 0.9


def signature(ids: list[str], edges, radius) -> str:
    h = hashlib.sha1()
    h.update("|".join(ids).encode())
    h.update("|".join(f"{a}>{b}>{k}" for a, b, k in edges).encode())
    h.update(",".join(f"{r:.1f}" for r in radius).encode())
    return h.hexdigest()[:16]


def encode_xy(xy: np.ndarray) -> str:
    return ";".join(f"{x:.0f},{y:.0f}" for x, y in xy)


def decode_xy(s: str) -> list[list[int]]:
    return [[int(v) for v in p.split(",")] for p in s.split(";")] if s else []


def _job(args):
    key, ids, edges, radius = args
    return key, encode_xy(layout(ids, edges, radius, seed_key=key))


def layout_many(jobs, cache: dict[str, tuple[str, str]], workers: int | None = None) -> dict[str, str]:
    """jobs: list of (key, ids, edges, radius). cache: {key: (sig, xy)} from the last run.
    Returns {key: xy} and leaves new entries in `cache`."""
    import os
    from multiprocessing import Pool

    out, todo = {}, []
    for key, ids, edges, radius in jobs:
        sig = signature(ids, edges, radius)
        hit = cache.get(key)
        if hit and hit[0] == sig:
            out[key] = hit[1]
        else:
            todo.append((key, ids, edges, radius, sig))
    sigs = {t[0]: t[4] for t in todo}
    if todo:
        # Biggest graphs first so the pool stays busy to the end.
        todo.sort(key=lambda t: -len(t[1]))
        with Pool(workers or os.cpu_count() or 2) as pool:
            for key, xy in pool.imap_unordered(_job, [t[:4] for t in todo], chunksize=16):
                out[key] = xy
                cache[key] = (sigs[key], xy)
    return out

#!/usr/bin/env python3
"""
backlinks.py - Read a memory store's [[links]] as a graph (no database, no deps).

Dream's memories are plain markdown files that link to each other with [[name]]
wiki-links - that is already a lightweight knowledge graph, just one-directional and
not queryable. This script reads it: for every memory it reports who it links to
(outbound), who links back to it (inbound / backlinks), which links are broken, which
memories are orphans, and a simple connectivity score (degree + a few PageRank
iterations) the skill can fold into a memory's importance.

It is read-only and deterministic: it scans files and prints JSON. It never edits your
memories - the skill does that, gated by Phase 5. Inspired by A-MEM's Zettelkasten-style
linked notes (arXiv:2502.12110) and HippoRAG's PageRank-over-the-graph importance
(NeurIPS'24), kept deliberately minimal. See DESIGN.md.

Usage:
    backlinks.py <memory_dir>
    backlinks.py ~/.claude/projects/<project>/memory/

Output JSON:
    {
      "nodes": [
        {"name","file","outbound":[...],"inbound":[...],"broken_outbound":[...],
         "orphan":bool,"connectivity":0.0-1.0}
      ],
      "broken_links":  [{"from","to"}],   # [[to]] has no matching memory
      "orphans":       ["name", ...],     # no inbound and no outbound links
      "summary":       {"memories","links","broken","orphans"}
    }
Nodes are sorted by connectivity descending (most load-bearing first).
"""

import json
import os
import re
import sys

# [[target]] or [[target|alias]] - we only care about the target (the filename stem).
LINK_RE = re.compile(r"\[\[\s*([^\]|#]+?)\s*(?:[|#][^\]]*)?\]\]")
NAME_RE = re.compile(r"^\s*name:\s*(.+?)\s*$", re.MULTILINE)

# Files that are not memories (index / generated / dotfiles) - excluded as nodes.
SKIP_FILES = {"MEMORY.md", "BACKLINKS.md"}


def memory_name(path, text):
    """Prefer frontmatter `name:`; fall back to the filename stem."""
    m = NAME_RE.search(text.split("---", 2)[1]) if text.startswith("---") else None
    if m:
        return m.group(1).strip()
    return os.path.splitext(os.path.basename(path))[0]


def body_of(text):
    """Strip the frontmatter block so links in frontmatter aren't double-counted."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2]
    return text


def pagerank(nodes, out_edges, damping=0.85, iters=20):
    """Tiny PageRank over the link graph. Pure stdlib, fixed iterations -> deterministic."""
    n = len(nodes)
    if n == 0:
        return {}
    pr = {name: 1.0 / n for name in nodes}
    for _ in range(iters):
        nxt = {name: (1.0 - damping) / n for name in nodes}
        for src in nodes:
            outs = [t for t in out_edges.get(src, []) if t in nodes]
            if not outs:
                # dangling node spreads its rank evenly (keeps the sum stable)
                share = damping * pr[src] / n
                for name in nodes:
                    nxt[name] += share
                continue
            share = damping * pr[src] / len(outs)
            for t in outs:
                nxt[t] += share
        pr = nxt
    return pr


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: backlinks.py <memory_dir>", file=sys.stderr)
        return 2
    mem_dir = os.path.expanduser(argv[0])
    if not os.path.isdir(mem_dir):
        print(f"error: not a directory: {mem_dir}", file=sys.stderr)
        return 2

    # 1. Read nodes and their outbound links.
    names = set()
    out_edges = {}
    files = {}
    for fn in sorted(os.listdir(mem_dir)):
        if not fn.endswith(".md") or fn in SKIP_FILES or fn.startswith("."):
            continue
        path = os.path.join(mem_dir, fn)
        try:
            text = open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        name = memory_name(path, text)
        names.add(name)
        files[name] = fn
        targets = [t.strip() for t in LINK_RE.findall(body_of(text))]
        # de-dupe while preserving order
        seen = set()
        out_edges[name] = [t for t in targets if not (t in seen or seen.add(t))]

    # 2. Inbound (backlinks) and broken links.
    inbound = {name: [] for name in names}
    broken_links = []
    broken_by_node = {name: [] for name in names}
    for src, targets in out_edges.items():
        for t in targets:
            if t in names:
                inbound[t].append(src)
            else:
                broken_links.append({"from": src, "to": t})
                broken_by_node[src].append(t)

    # 3. Connectivity: blend normalised degree with PageRank. Simple and bounded [0,1].
    pr = pagerank(names, out_edges)
    pr_max = max(pr.values()) if pr else 1.0
    deg_max = max((len(out_edges[n]) + len(inbound[n]) for n in names), default=1) or 1

    nodes = []
    orphans = []
    for name in names:
        deg = len(out_edges[name]) + len(inbound[name])
        is_orphan = deg == 0
        if is_orphan:
            orphans.append(name)
        connectivity = 0.5 * (deg / deg_max) + 0.5 * (pr[name] / pr_max)
        nodes.append({
            "name": name,
            "file": files[name],
            "outbound": [t for t in out_edges[name] if t in names],
            "inbound": sorted(inbound[name]),
            "broken_outbound": broken_by_node[name],
            "orphan": is_orphan,
            "connectivity": round(connectivity, 4),
        })

    nodes.sort(key=lambda nd: nd["connectivity"], reverse=True)
    result = {
        "nodes": nodes,
        "broken_links": broken_links,
        "orphans": sorted(orphans),
        "summary": {
            "memories": len(names),
            "links": sum(len(v) for v in out_edges.values()),
            "broken": len(broken_links),
            "orphans": len(orphans),
        },
    }
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

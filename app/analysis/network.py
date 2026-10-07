"""Build entity co-occurrence networks (sponsor↔drug, drug↔drug, condition↔drug, ...)."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations, product

from app.analysis.aggregate import Bucket, make_citations
from app.analysis.extract import Extracted, entities
from app.ctgov.normalize import TrialRecord
from app.schemas.common import EntityType
from app.schemas.plan import NetworkSpec
from app.schemas.response import Evidence, NetworkData, NetworkEdge, NetworkNode


def _node_id(t: EntityType, key: str) -> str:
    return f"{t.value}:{key}"


def build_network(records: list[TrialRecord], spec: NetworkSpec, *, citation_cap: int) -> NetworkData:
    """One node type -> co-occurrence among that type; two types -> bipartite graph."""
    node_buckets: dict[str, Bucket] = {}
    node_types: dict[str, EntityType] = {}
    edge_buckets: dict[tuple[str, str], Bucket] = {}

    for rec in records:
        per_type: dict[EntityType, list[Extracted]] = {t: entities(rec, t) for t in spec.node_types}
        for t, items in per_type.items():
            for ex in items:
                nid = _node_id(t, ex.key)
                node_types[nid] = t
                node_buckets.setdefault(nid, Bucket(nid, ex.label)).add(rec, ex.evidence)

        if len(spec.node_types) == 1:
            t = spec.node_types[0]
            pairs = combinations(sorted(per_type[t], key=lambda e: e.key), 2)
        else:
            a, b = spec.node_types
            pairs = product(per_type[a], per_type[b])
        for e1, e2 in pairs:
            t1 = spec.node_types[0]
            t2 = spec.node_types[-1]
            key = (_node_id(t1, e1.key), _node_id(t2, e2.key))
            bucket = edge_buckets.setdefault(key, Bucket(f"{key[0]}|{key[1]}", f"{e1.label} — {e2.label}"))
            bucket.add(rec, Evidence(field=e1.evidence.field, excerpt=e1.evidence.excerpt))
            bucket.evidence[rec.nct_id].append(e2.evidence)

    # Prune: keep the heaviest nodes, then edges among them above the weight threshold.
    ranked = sorted(node_buckets.values(), key=lambda b: (-len(b.records), b.label))
    if len(spec.node_types) == 2:
        # Keep a balanced set so one side cannot crowd out the other.
        per_side = max(spec.max_nodes // 2, 1)
        kept_ids: set[str] = set()
        for t in spec.node_types:
            side = [b for b in ranked if node_types[b.key] == t][:per_side]
            kept_ids.update(b.key for b in side)
    else:
        kept_ids = {b.key for b in ranked[: spec.max_nodes]}

    edges: list[NetworkEdge] = []
    connected: set[str] = set()
    for (s, t), b in edge_buckets.items():
        if s in kept_ids and t in kept_ids and len(b.records) >= spec.min_edge_weight:
            cites, total, truncated = make_citations(b, citation_cap)
            edges.append(NetworkEdge(source=s, target=t, weight=float(total), citations=cites, citation_count=total, citations_truncated=truncated))
            connected.update((s, t))
    edges.sort(key=lambda e: (-e.weight, e.source, e.target))

    nodes: list[NetworkNode] = []
    for b in ranked:
        if b.key not in kept_ids or (b.key not in connected and spec.min_edge_weight > 1):
            continue
        cites, total, truncated = make_citations(b, citation_cap)
        nodes.append(NetworkNode(id=b.key, label=b.label, type=node_types[b.key].value, weight=float(total), citations=cites, citation_count=total, citations_truncated=truncated))

    # Drop isolated nodes when there are edges to show; keep them otherwise so the graph is not empty.
    if edges:
        nodes = [n for n in nodes if n.id in connected]
    return NetworkData(nodes=nodes, edges=edges)


def degree_summary(data: NetworkData) -> dict[str, int]:
    deg: dict[str, int] = defaultdict(int)
    for e in data.edges:
        deg[e.source] += 1
        deg[e.target] += 1
    return dict(deg)

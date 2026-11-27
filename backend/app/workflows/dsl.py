"""Workflow definition format (DSL). The editor, the AI copilot and the n8n bridge all produce this JSON."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = 1


class Node(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    type: str
    params: Dict[str, Any] = Field(default_factory=dict)
    label: Optional[str] = None
    position: Optional[Dict[str, float]] = None  # editor layout only; ignored by the runtime


class Edge(BaseModel):
    source: str
    target: str
    port: str = "out"  # output port of the source node this edge leaves from


class Workflow(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{0,63}$")
    name: str
    version: int = Field(default=1, ge=1)
    trigger: str  # id of the node that starts the run
    nodes: List[Node] = Field(min_length=1, max_length=200)
    edges: List[Edge] = Field(default_factory=list, max_length=1000)
    budget_eur: float = Field(default=0.0, ge=0)  # max total cost of action nodes per run
    approval_threshold_eur: Optional[float] = Field(default=None, ge=0)  # actions above this need a human
    template: Optional[Dict[str, Any]] = None  # {"id", "version"} when installed from the template library

    @model_validator(mode="after")
    def _structure(self) -> "Workflow":
        ids = [n.id for n in self.nodes]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate node ids: {dupes}")
        known = set(ids)
        if self.trigger not in known:
            raise ValueError(f"trigger node '{self.trigger}' does not exist")
        for e in self.edges:
            for end in (e.source, e.target):
                if end not in known:
                    raise ValueError(f"edge {e.source}->{e.target} references unknown node '{end}'")
            if e.target == self.trigger:
                raise ValueError("no edge may point into the trigger node")
        if _has_cycle(ids, self.edges):
            raise ValueError("workflow graph contains a cycle; use a wait node and a new run instead of loops")
        return self

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def outgoing(self, node_id: str, port: str) -> List[Edge]:
        return [e for e in self.edges if e.source == node_id and e.port == port]


def _has_cycle(ids: List[str], edges: List[Edge]) -> bool:
    adj: Dict[str, List[str]] = {i: [] for i in ids}
    for e in edges:
        adj[e.source].append(e.target)
    state: Dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(n: str) -> bool:
        state[n] = 1
        for m in adj[n]:
            if state.get(m) == 1 or (state.get(m) is None and visit(m)):
                return True
        state[n] = 2
        return False

    return any(state.get(i) is None and visit(i) for i in ids)

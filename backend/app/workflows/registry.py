"""Node SDK: every agent, action, logic step and trigger is a registered node type with typed params and a policy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError

from app.workflows.dsl import Workflow

CATEGORIES = ("trigger", "agent", "action", "logic", "control", "platform")


@dataclass(frozen=True)
class Policy:
    reversible: bool = True
    needs_approval: bool = False  # always ask a human before running
    locked: bool = False  # platform-only (underwriting, claims); never runnable from user workflows
    cost_param: Optional[str] = None  # param holding the estimated cost in EUR, checked against the budget


@dataclass
class NodeContext:
    run_id: str
    node_id: str
    now: datetime
    payload: Dict[str, Any]
    outputs: Dict[str, Dict[str, Any]]
    dry_run: bool = False
    resumed: Optional[Dict[str, Any]] = None  # set when a paused node (wait/approval) resumes
    tenant: str = "default"  # whose channels and data the node may touch


@dataclass
class NodeResult:
    output: Dict[str, Any] = field(default_factory=dict)
    port: str = "out"
    status: str = "ok"  # ok | wait | approval
    cost_eur: float = 0.0
    resume_at: Optional[datetime] = None  # for status == "wait"
    reason: Optional[str] = None  # for status == "approval"


Handler = Callable[[NodeContext, BaseModel], NodeResult]


@dataclass
class NodeType:
    name: str
    category: str
    description: str
    params_model: Type[BaseModel]
    handler: Handler
    ports: Tuple[str, ...] = ("out",)
    policy: Policy = field(default_factory=Policy)

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category}")

    def spec(self) -> Dict[str, Any]:
        """Editor-facing description: forms are generated from the JSON schema."""
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "ports": list(self.ports),
            "policy": {
                "reversible": self.policy.reversible,
                "needs_approval": self.policy.needs_approval,
                "locked": self.policy.locked,
                "cost_param": self.policy.cost_param,
            },
            "params_schema": self.params_model.model_json_schema(),
        }


class Registry:
    def __init__(self) -> None:
        self._types: Dict[str, NodeType] = {}

    def register(self, node_type: NodeType) -> NodeType:
        if node_type.name in self._types:
            raise ValueError(f"node type {node_type.name} already registered")
        self._types[node_type.name] = node_type
        return node_type

    def node(self, name: str, category: str, description: str, params: Type[BaseModel],
             ports: Tuple[str, ...] = ("out",), policy: Optional[Policy] = None) -> Callable[[Handler], Handler]:
        """Decorator form: @registry.node("action.notify", "action", "...", NotifyParams)."""
        def wrap(fn: Handler) -> Handler:
            self.register(NodeType(name, category, description, params, fn, ports, policy or Policy()))
            return fn
        return wrap

    def get(self, name: str) -> NodeType:
        if name not in self._types:
            raise KeyError(f"unknown node type {name}")
        return self._types[name]

    def catalogue(self, include_locked: bool = False) -> List[Dict[str, Any]]:
        return [t.spec() for t in self._types.values() if include_locked or not t.policy.locked]

    def validate(self, wf: Workflow, allow_locked: bool = False) -> List[str]:
        """Checks that need node types: known types, valid ports, params, trigger category, locked nodes.

        Params that reference runtime values ("$trigger..." / "$nodes...") are only checked at run time.
        """
        errors: List[str] = []
        for n in wf.nodes:
            try:
                t = self.get(n.type)
            except KeyError as exc:
                errors.append(f"{n.id}: {exc.args[0]}")
                continue
            if t.policy.locked and not allow_locked:
                errors.append(f"{n.id}: node type {n.type} is platform-only")
            if (n.id == wf.trigger) != (t.category == "trigger"):
                errors.append(f"{n.id}: exactly the trigger node must be a trigger-type node")
            static = {k: v for k, v in n.params.items() if not is_reference(v)}
            if len(static) == len(n.params):
                try:
                    t.params_model.model_validate(static)
                except ValidationError as exc:
                    errors.extend(f"{n.id}.{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        for e in wf.edges:
            try:
                t = self.get(wf.node(e.source).type)
            except KeyError:
                continue
            if e.port not in t.ports:
                errors.append(f"edge {e.source}->{e.target}: port '{e.port}' not in {list(t.ports)}")
        return errors


def is_reference(value: Any) -> bool:
    return isinstance(value, str) and (value.startswith("$trigger") or value.startswith("$nodes."))

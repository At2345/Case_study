"""Workflow automation engine: versioned workflow DSL, node SDK, guardrails and a resumable runtime.

The runtime keeps all run state as plain JSON so the same `advance` step can later run inside a
durable executor (Temporal) without changing workflow definitions or node handlers.
"""
from app.workflows.dsl import Edge, Node, Workflow
from app.workflows.registry import NodeContext, NodeResult, NodeType, Policy, Registry
from app.workflows.runtime import Engine, WorkflowError
from app.workflows.builtin import default_registry

__all__ = [
    "Edge", "Node", "Workflow",
    "NodeContext", "NodeResult", "NodeType", "Policy", "Registry",
    "Engine", "WorkflowError", "default_registry",
]

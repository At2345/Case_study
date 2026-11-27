import { describe, expect, it } from "vitest";
import catalogue from "../test/fixtures/workflowNodeTypes.json";
import {
  coerceField, defaultParams, fieldsFromSchema, fromFlow, missingRequired, newNodeId, toFlow,
} from "./workflowGraph";

const types = catalogue.node_types;
const spec = (name) => types.find((t) => t.name === name);

const wf = {
  id: "rescue-de", name: "Rescue", version: 3, trigger: "start", budget_eur: 2000, approval_threshold_eur: null,
  nodes: [
    { id: "start", type: "trigger.sell_through_gap", params: {} },
    { id: "is_price", type: "logic.if", params: { left: "$nodes.propose.action", op: "==", right: "change_price" }, position: { x: 10, y: 20 } },
    { id: "wait", type: "logic.wait", params: { days: 14 }, label: "Two weeks" },
  ],
  edges: [{ source: "start", target: "is_price", port: "out" }, { source: "is_price", target: "wait", port: "true" }],
};

describe("workflowGraph", () => {
  it("round-trips the backend DSL through React Flow shapes", () => {
    const g = toFlow(wf, types);
    expect(g.meta).toMatchObject({ id: "rescue-de", version: 3, budget_eur: 2000, approval_threshold_eur: "" });
    expect(g.nodes[1].position).toEqual({ x: 10, y: 20 });
    expect(g.nodes[0].data.spec.category).toBe("trigger");
    expect(g.edges[1]).toMatchObject({ sourceHandle: "true", label: "true" });

    const back = fromFlow(g.meta, g.nodes, g.edges);
    expect(back.trigger).toBe("start");
    expect(back.approval_threshold_eur).toBeNull();
    expect(back.nodes[2]).toMatchObject({ id: "wait", label: "Two weeks", params: { days: 14 } });
    expect(back.edges).toEqual(wf.edges);
    expect(back.template).toBeNull();

    const fromTemplate = toFlow({ ...wf, template: { id: "rescue-basic", version: 1 } }, types);
    expect(fromFlow(fromTemplate.meta, fromTemplate.nodes, fromTemplate.edges).template)
      .toEqual({ id: "rescue-basic", version: 1 });
  });

  it("drops empty params and finds no trigger when none exists", () => {
    const g = toFlow({ ...wf, nodes: [{ id: "w", type: "logic.wait", params: { days: "" } }], edges: [] }, types);
    const back = fromFlow({ ...g.meta, approval_threshold_eur: "150" }, g.nodes, g.edges);
    expect(back.trigger).toBe("");
    expect(back.nodes[0].params).toEqual({});
    expect(back.approval_threshold_eur).toBe(150);
  });

  it("builds form fields from the real backend schemas", () => {
    const price = Object.fromEntries(fieldsFromSchema(spec("action.change_price").params_schema).map((f) => [f.name, f]));
    expect(price.pct_change).toMatchObject({ kind: "number", required: true, min: -50, max: 50 });
    expect(price.estimated_cost_eur).toMatchObject({ kind: "number", required: false, default: 0 });
    const notify = Object.fromEntries(fieldsFromSchema(spec("action.notify").params_schema).map((f) => [f.name, f]));
    expect(notify.channel).toMatchObject({ kind: "enum", options: ["in_app", "email", "slack"] });
    const iff = Object.fromEntries(fieldsFromSchema(spec("logic.if").params_schema).map((f) => [f.name, f]));
    expect(iff.left.kind).toBe("json");
    expect(defaultParams(spec("trigger.sell_through_gap"))).toEqual({ gap_threshold: -0.2, min_days_since_receipt: 60 });
    expect(missingRequired(spec("action.change_price"), { sku: "A" })).toEqual(["marketplace", "pct_change"]);
  });

  it("coerces inputs but keeps references and bad numbers", () => {
    const num = { kind: "number" };
    expect(coerceField(num, "-10.5")).toBe(-10.5);
    expect(coerceField(num, "$nodes.propose.pct_change")).toBe("$nodes.propose.pct_change");
    expect(coerceField(num, "abc")).toBe("abc");
    expect(coerceField(num, "")).toBeUndefined();
    expect(coerceField({ kind: "json" }, "12")).toBe(12);
    expect(coerceField({ kind: "json" }, "change_price")).toBe("change_price");
  });

  it("creates unique, DSL-valid node ids", () => {
    expect(newNodeId("action.change_price", [])).toBe("change_price_1");
    expect(newNodeId("action.change_price", ["change_price_1"])).toBe("change_price_2");
    expect(newNodeId("x.9lives", [])).toMatch(/^[a-z][a-z0-9_]*$/);
  });
});

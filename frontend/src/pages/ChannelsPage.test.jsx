import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("../services/api", () => ({ api: {
  channelTypes: vi.fn(), channelConnections: vi.fn(), addChannelConnection: vi.fn(), updateChannelConnection: vi.fn(),
  deleteChannelConnection: vi.fn(), testChannelConnection: vi.fn(), syncChannelConnection: vi.fn(),
  channelInventory: vi.fn(), channelOrders: vi.fn(), channelEvents: vi.fn(),
  agentStatus: vi.fn(), askAgent: vi.fn(), agentProposals: vi.fn(), decideAgentProposal: vi.fn(),
} }));
import { api } from "../services/api";
import ChannelsPage from "./ChannelsPage";

const shopify = { name: "shopify", label: "Shopify", kind: "shop", capabilities: ["orders", "inventory", "webhooks"], notes: "",
  fields: [{ name: "shop_domain", label: "Shop domain", secret: false, required: true, default: null, help: "your-store.myshopify.com" },
    { name: "access_token", label: "Admin API access token", secret: true, required: true, default: null, help: "" }] };
const conn = (over = {}) => ({ id: "c1", type: "shopify", name: "My Shopify", enabled: true, last_sync_at: null, last_error: null,
  capabilities: ["orders", "inventory", "webhooks"], webhook_path: "/api/v1/channels/webhooks/default/c1", ...over });

describe("ChannelsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.channelTypes.mockResolvedValue({ types: [shopify] });
    api.channelConnections.mockResolvedValue({ connections: [conn()] });
    api.channelInventory.mockResolvedValue({ by_sku: [{ sku: "MAT", total: 12, channels: { c1: 12 } }] });
    api.channelOrders.mockResolvedValue({ orders: [{ connection_id: "c1", external_id: "1001", status: "paid", total: 29.9, currency: "EUR" }] });
    api.channelEvents.mockResolvedValue({ events: [{ id: 1, type: "stock_low", connection_id: "c1", at: "2026-11-27T09:00:00Z", payload: { sku: "MAT" } }] });
    api.agentStatus.mockResolvedValue({ available: true, model: "qwen2.5:14b", local: true, hint: null });
    api.agentProposals.mockResolvedValue({ proposals: [] });
  });

  it("shows connections, stock, orders and events", async () => {
    render(<ChannelsPage role="planner" />);
    expect(await screen.findByTestId("channel-c1")).toHaveTextContent("My Shopify");
    expect(screen.getByTestId("channel-c1")).toHaveTextContent("/api/v1/channels/webhooks/default/c1");
    expect(screen.getByTestId("channel-stock-table")).toHaveTextContent("My Shopify: 12");
    expect(screen.getByTestId("channel-orders-table")).toHaveTextContent("1001");
    expect(screen.getByTestId("channel-events")).toHaveTextContent("stock_low");
    expect(screen.queryByTestId("channel-add-type")).not.toBeInTheDocument(); // only admins connect channels
    expect(screen.queryByTestId("channel-delete-c1")).not.toBeInTheDocument();
  });

  it("admin connects a shop with the secret as a password field", async () => {
    api.addChannelConnection.mockResolvedValue(conn({ id: "c2" }));
    const user = userEvent.setup();
    render(<ChannelsPage role="admin" />);
    await screen.findByTestId("channel-c1");
    await user.selectOptions(screen.getByTestId("channel-add-type"), "shopify");
    expect(screen.getByTestId("channel-field-access_token")).toHaveAttribute("type", "password");
    await user.type(screen.getByTestId("channel-field-shop_domain"), "acme.myshopify.com");
    await user.type(screen.getByTestId("channel-field-access_token"), "shpat_secret");
    await user.type(screen.getByTestId("channel-field-low_stock"), "5");
    await user.click(screen.getByTestId("channel-add-save"));
    await waitFor(() => expect(api.addChannelConnection).toHaveBeenCalledWith({ type: "shopify", name: "Shopify",
      config: { shop_domain: "acme.myshopify.com", access_token: "shpat_secret" }, sync_minutes: 15, low_stock: 5 }));
    expect(await screen.findByTestId("channels-message")).toHaveTextContent("never shown again");
    expect(screen.queryByTestId("channel-add-form")).not.toBeInTheDocument();
  });

  it("tests and syncs a connection", async () => {
    api.testChannelConnection.mockResolvedValue({ ok: false, error: "shopify: the channel rejected the credentials (HTTP 401)" });
    api.syncChannelConnection.mockResolvedValue({ orders_created: 3, inventory_items: 12, events: 0, errors: [], initial: true });
    const user = userEvent.setup();
    render(<ChannelsPage role="planner" />);
    await user.click(await screen.findByTestId("channel-test-c1"));
    expect(await screen.findByTestId("channels-message")).toHaveTextContent("rejected the credentials");
    await user.click(screen.getByTestId("channel-sync-c1"));
    await waitFor(() => expect(screen.getByTestId("channels-message"))
      .toHaveTextContent("3 new orders, 12 stock items, 0 events (first import, no events)"));
  });

  it("asks the agent and approves its proposal", async () => {
    api.askAgent.mockResolvedValue({ answer: "MAT is low on My Shopify. I proposed a restock.", steps: [{ tool: "get_channel_inventory" }],
      proposals: [{ id: "p1" }] });
    api.agentProposals.mockResolvedValueOnce({ proposals: [] }).mockResolvedValueOnce({ proposals: [
      { id: "p1", tool: "set_channel_stock", arguments: { connection_id: "c1", sku: "MAT", quantity: 40 }, question: "restock MAT", created_by: "danny" }] })
      .mockResolvedValue({ proposals: [] });
    api.decideAgentProposal.mockResolvedValue({ status: "executed" });
    const user = userEvent.setup();
    render(<ChannelsPage role="planner" />);
    expect(await screen.findByTestId("agent-console")).toHaveTextContent("qwen2.5:14b (local, free)");
    await user.type(screen.getByTestId("agent-question"), "Which SKUs are low?");
    await user.click(screen.getByTestId("agent-ask"));
    await waitFor(() => expect(api.askAgent).toHaveBeenCalledWith("Which SKUs are low?"));
    expect(await screen.findByTestId("agent-reply")).toHaveTextContent("I proposed a restock.");
    expect(await screen.findByTestId("agent-proposal-p1")).toHaveTextContent("set_channel_stock");
    await user.click(screen.getByTestId("agent-approve-p1"));
    await waitFor(() => expect(api.decideAgentProposal).toHaveBeenCalledWith("p1", true));
    await waitFor(() => expect(screen.queryByTestId("agent-proposal-p1")).not.toBeInTheDocument());
  });

  it("explains how to enable the free local model", async () => {
    api.agentStatus.mockResolvedValue({ available: false, model: null, local: false,
      hint: "No AI model is configured. For a free local model install Ollama ... set OLLAMA_MODEL." });
    render(<ChannelsPage role="auditor" />);
    expect(await screen.findByTestId("agent-unavailable")).toHaveTextContent("OLLAMA_MODEL");
    expect(screen.getByTestId("agent-ask")).toBeDisabled();
    expect(screen.queryByTestId("channel-sync-c1")).not.toBeInTheDocument();
  });
});

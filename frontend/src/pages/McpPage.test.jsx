import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("../services/api", () => ({
  api: {
    mcpServers: vi.fn(), mcpCalls: vi.fn(), createMcpServer: vi.fn(),
    updateMcpServer: vi.fn(), deleteMcpServer: vi.fn(), testMcpServer: vi.fn(),
  },
}));
import { api } from "../services/api";
import McpPage from "./McpPage";

const server = (over = {}) => ({
  id: "erp_sap", name: "SAP ERP", kind: "erp", kind_label: "ERP", transport: "streamable_http",
  url: "http://127.0.0.1:9101/", headers: { Authorization: "Be****3456" }, enabled: true,
  tools: ["get_stock_levels"], status: "connected", last_error: "", discovered_tools: [],
  ...over,
});

const kinds = { erp: { label: "ERP", tools: ["get_open_purchase_orders"], write_tools: [] },
  crm: { label: "CRM", tools: [], write_tools: [] }, jira: { label: "Jira", tools: [], write_tools: [] },
  generic: { label: "Generic MCP", tools: [], write_tools: [] } };

describe("McpPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.mcpServers.mockResolvedValue({ servers: [server()], kinds });
    api.mcpCalls.mockResolvedValue({ calls: [{ at: "2026-10-16T10:00:00Z", server_id: "erp_sap", tool: "get_stock_levels", user: "danny", ok: true }] });
  });

  it("lists servers with masked headers and the audit log", async () => {
    render(<McpPage role="admin" />);
    expect(await screen.findByTestId("mcp-server-erp_sap")).toBeInTheDocument();
    expect(screen.getByText(/Be\*\*\*\*3456/)).toBeInTheDocument();
    expect(screen.getByText("get_stock_levels")).toBeInTheDocument();
    expect(screen.getByText("danny")).toBeInTheDocument();
  });

  it("admin can add a server", async () => {
    api.createMcpServer.mockResolvedValue({});
    const user = userEvent.setup();
    render(<McpPage role="admin" />);
    await screen.findByTestId("mcp-server-erp_sap");
    await user.click(screen.getByTestId("mcp-add-btn"));
    await user.type(screen.getByTestId("mcp-field-id"), "erp_new");
    await user.type(screen.getByTestId("mcp-field-name"), "New ERP");
    await user.type(screen.getByTestId("mcp-field-url"), "http://127.0.0.1:9101/");
    await user.click(screen.getByTestId("mcp-save-btn"));
    await waitFor(() => expect(api.createMcpServer).toHaveBeenCalledWith(
      expect.objectContaining({ id: "erp_new", name: "New ERP", kind: "erp", enabled: true })));
  });

  it("test connection calls the endpoint and refreshes", async () => {
    api.testMcpServer.mockResolvedValue({ status: "connected", tools: [{ name: "get_stock_levels" }] });
    const user = userEvent.setup();
    render(<McpPage role="admin" />);
    await screen.findByTestId("mcp-server-erp_sap");
    await user.click(screen.getByTestId("mcp-test-erp_sap"));
    await waitFor(() => expect(api.testMcpServer).toHaveBeenCalledWith("erp_sap"));
    expect(await screen.findByText(/1 tools discovered/)).toBeInTheDocument();
  });

  it("non-admin sees the list but no management actions", async () => {
    render(<McpPage role="auditor" />);
    expect(await screen.findByTestId("mcp-server-erp_sap")).toBeInTheDocument();
    expect(screen.queryByTestId("mcp-add-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("mcp-delete-erp_sap")).not.toBeInTheDocument();
  });

  it("shows API errors", async () => {
    api.mcpServers.mockRejectedValue(new Error("MCP server erp_sap not found"));
    render(<McpPage role="admin" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("MCP server erp_sap not found");
  });
});

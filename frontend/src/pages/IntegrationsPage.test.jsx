import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("../services/api", () => ({ api: {
  apiKeys: vi.fn(), createApiKey: vi.fn(), revokeApiKey: vi.fn(), workflowSettings: vi.fn(), updateWorkflowSettings: vi.fn(),
} }));
import { api } from "../services/api";
import IntegrationsPage from "./IntegrationsPage";

vi.setConfig({ testTimeout: 20000 });

const key = (over = {}) => ({ id: "k1", name: "n8n production", role: "planner", created_by: "root",
  created_at: "2026-11-27T09:00:00+00:00", last_used_at: null, revoked_at: null, ...over });

describe("IntegrationsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.apiKeys.mockResolvedValue({ keys: [key()] });
    api.workflowSettings.mockResolvedValue({ share_benchmarks: false });
  });

  it("creates a key and shows it once", async () => {
    api.createApiKey.mockResolvedValue({ ...key({ id: "k2", name: "erp feed", role: "auditor" }), key: "sopk_abc123_secretvalue" });
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    expect(await screen.findByTestId("api-key-list")).toHaveTextContent("n8n production");
    expect(screen.getByTestId("api-key-create")).toBeDisabled();
    await user.type(screen.getByTestId("api-key-name"), "  erp feed ");
    await user.selectOptions(screen.getByTestId("api-key-role"), "auditor");
    await user.click(screen.getByTestId("api-key-create"));
    await waitFor(() => expect(api.createApiKey).toHaveBeenCalledWith({ name: "erp feed", role: "auditor" }));
    expect(await screen.findByTestId("api-key-value")).toHaveTextContent("sopk_abc123_secretvalue");
    expect(screen.getByTestId("api-key-created")).toHaveTextContent("shown only once");
    await user.click(screen.getByText("I have stored it"));
    expect(screen.queryByTestId("api-key-value")).not.toBeInTheDocument();
    expect(api.apiKeys).toHaveBeenCalledTimes(2);
  });

  it("revokes a key after confirmation", async () => {
    api.revokeApiKey.mockResolvedValue({ revoked: "k1" });
    api.apiKeys.mockResolvedValueOnce({ keys: [key()] })
      .mockResolvedValue({ keys: [key({ revoked_at: "2026-11-27T10:00:00+00:00" })] });
    const confirm = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    await screen.findByTestId("api-key-revoke-k1");

    confirm.mockReturnValueOnce(false);
    await user.click(screen.getByTestId("api-key-revoke-k1"));
    expect(api.revokeApiKey).not.toHaveBeenCalled();

    confirm.mockReturnValueOnce(true);
    await user.click(screen.getByTestId("api-key-revoke-k1"));
    await waitFor(() => expect(api.revokeApiKey).toHaveBeenCalledWith("k1"));
    expect(await within(screen.getByTestId("api-key-list")).findByText(/revoked 2026-11-27 10:00/)).toBeInTheDocument();
    confirm.mockRestore();
  });

  it("toggles benchmark sharing", async () => {
    api.updateWorkflowSettings.mockResolvedValue({ share_benchmarks: true });
    const user = userEvent.setup();
    render(<IntegrationsPage />);
    expect(await screen.findByTestId("sharing-state")).toHaveTextContent("Sharing is off");
    await user.click(screen.getByTestId("sharing-toggle"));
    await waitFor(() => expect(api.updateWorkflowSettings).toHaveBeenCalledWith({ share_benchmarks: true }));
    expect(await screen.findByTestId("sharing-state")).toHaveTextContent("Sharing is on");
    expect(screen.getByTestId("connect-help")).toHaveTextContent("/api/v1/workflows/mcp");
  });

  it("shows API errors", async () => {
    api.apiKeys.mockRejectedValue(new Error("Insufficient permissions"));
    render(<IntegrationsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Insufficient permissions");
  });
});

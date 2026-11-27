import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("../services/api", () => ({ api: {
  coverQuote: vi.fn(), coverQuotes: vi.fn(), workflows: vi.fn(), bindCoverQuote: vi.fn(), coverPolicies: vi.fn(),
  coverPortfolio: vi.fn(), observeCoverPolicy: vi.fn(), settleCoverPolicy: vi.fn(),
  coverLots: vi.fn(), bidCoverLot: vi.fn(), awardCoverLot: vi.fn(),
} }));
import { api } from "../services/api";
import CoverPage from "./CoverPage";

// These tests type into many fields; under a loaded parallel run they can exceed the 5 s default.
vi.setConfig({ testTimeout: 20000 });

const offered = {
  id: "q1", po_id: "PO-7", offered: true, decline_reasons: [], risk_grade: "B", units: 1500, recommended_units: 1210,
  covered_units: 1210, excluded_units: 290, deductible_units: 121, expected_unsold_units: 140.2, expected_unsold_share: 0.0935,
  claim_probability: 0.21, payout_per_unit_eur: 3.2, premium_eur: 182.4, premium_rate: 0.0188,
  experience: { policies: 3, a_to_e: 1.4, credibility: 0.316, factor: 1.12 },
  terms: { horizon_days: 180 }, demand: { mean_units: 1000, sd_units: 250, source: "forecast" },
  valid_until: "2026-12-03T10:00:00+00:00", created_at: "2026-11-26T10:00:00+00:00", created_by: "danny",
};

describe("CoverPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.coverQuotes.mockResolvedValue({ quotes: [] });
    api.workflows.mockResolvedValue({ workflows: [{ id: "rescue", name: "Rescue: discount or transfer", version: 1 }] });
    api.coverPolicies.mockResolvedValue({ policies: [] });
    api.coverPortfolio.mockResolvedValue({ policies: 0, by_status: {}, premium_written_eur: 0, claims_paid_eur: 0,
      loss_ratio: null, open_max_exposure_eur: 0, rescue_runs: 0 });
    api.coverLots.mockResolvedValue({ lots: [] });
  });

  const lot = (over = {}) => ({
    id: "l1", po_id: "PO-8", sku: "MAT-BLUE", status: "OPEN", units: 200, cost_value_eur: 1600,
    reserve_price_per_unit_eur: 0.8, best_bid: null, award: null, ...over,
  });

  it("collects bids on a recovery lot and the admin awards it", async () => {
    api.coverLots.mockResolvedValueOnce({ lots: [lot()] })
      .mockResolvedValueOnce({ lots: [lot({ best_bid: { bidder: "Outlet GmbH", price_per_unit_eur: 1.2, channel: "outlet" } })] })
      .mockResolvedValue({ lots: [lot({ status: "SOLD", award: { bidder: "Outlet GmbH", proceeds_eur: 240, recovery_rate: 0.15 } })] });
    api.bidCoverLot.mockResolvedValue({});
    api.awardCoverLot.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CoverPage role="admin" />);
    await screen.findByTestId("cover-lot-l1");
    expect(screen.queryByTestId("cover-lot-award-l1")).not.toBeInTheDocument(); // nothing to award yet
    await user.type(screen.getByTestId("cover-lot-bidder-l1"), "Outlet GmbH");
    await user.selectOptions(screen.getByTestId("cover-lot-channel-l1"), "outlet");
    await user.type(screen.getByTestId("cover-lot-price-l1"), "1.2");
    await user.click(screen.getByTestId("cover-lot-bid-l1"));
    await waitFor(() => expect(api.bidCoverLot).toHaveBeenCalledWith("l1",
      { bidder: "Outlet GmbH", channel: "outlet", price_per_unit_eur: 1.2 }));
    expect(await screen.findByTestId("cover-lot-l1")).toHaveTextContent("best: Outlet GmbH €1.2/unit (outlet)");

    await user.click(screen.getByTestId("cover-lot-award-l1"));
    await waitFor(() => expect(api.awardCoverLot).toHaveBeenCalledWith("l1"));
    expect(await screen.findByText(/to Outlet GmbH: €240 \(15% of cost\)/)).toBeInTheDocument();
  });

  it("donation bids send price 0 and planners cannot award", async () => {
    api.coverLots.mockResolvedValue({ lots: [lot({ best_bid: { bidder: "x", price_per_unit_eur: 1, channel: "b2b" } })] });
    api.bidCoverLot.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CoverPage role="planner" />);
    await screen.findByTestId("cover-lot-l1");
    expect(screen.queryByTestId("cover-lot-award-l1")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByTestId("cover-lot-channel-l1"), "donation");
    expect(screen.queryByTestId("cover-lot-price-l1")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("cover-lot-bid-l1"));
    await waitFor(() => expect(api.bidCoverLot).toHaveBeenCalledWith("l1",
      { bidder: undefined, channel: "donation", price_per_unit_eur: 0 }));
  });

  const policy = (over = {}) => ({
    id: "p1", po_id: "PO-7", effective_status: "ACTIVE", status: "ACTIVE", covered_units: 1210, premium_eur: 182.4,
    ends_at: "2027-05-25T10:00:00+00:00", rescue_playbook_id: "rescue", rescue_runs: [], rescue_gap_threshold: -0.2,
    last_observation: null, claim: null, ...over,
  });

  it("binds an offered quote with a rescue playbook", async () => {
    api.coverQuote.mockResolvedValue(offered);
    api.bindCoverQuote.mockResolvedValue(policy());
    const user = userEvent.setup();
    render(<CoverPage role="planner" />);
    await user.type(screen.getByTestId("cover-po_id"), "PO-7");
    await user.type(screen.getByTestId("cover-units"), "1500");
    await user.type(screen.getByTestId("cover-unit_landed_cost_eur"), "8");
    await user.type(screen.getByTestId("cover-item_id"), "42");
    await user.click(screen.getByTestId("cover-submit"));
    await screen.findByTestId("cover-bind");
    await user.selectOptions(screen.getByTestId("cover-bind-playbook"), "rescue");
    await user.click(screen.getByTestId("cover-bind-submit"));
    await waitFor(() => expect(api.bindCoverQuote).toHaveBeenCalledWith("q1", { marketplace: "DE", rescue_playbook_id: "rescue" }));
    expect(await screen.findByTestId("cover-bound")).toHaveTextContent("Bound as pilot policy p1, ends 2027-05-25");
    expect(screen.queryByTestId("cover-bind")).not.toBeInTheDocument();
    await waitFor(() => expect(api.coverPolicies).toHaveBeenCalledTimes(2)); // policies reloaded after binding
  });

  it("records sales that trigger a rescue, and an admin settles", async () => {
    api.coverPolicies.mockResolvedValue({ policies: [
      policy(),
      policy({ id: "p2", po_id: "PO-8", effective_status: "AWAITING_SETTLEMENT",
        last_observation: { units_sold_to_date: 200, gap: -0.4 }, rescue_runs: [{ run_id: "r1" }] }),
    ] });
    api.coverPortfolio.mockResolvedValue({ policies: 2, by_status: {}, premium_written_eur: 364.8, claims_paid_eur: 0,
      loss_ratio: null, open_max_exposure_eur: 3456, rescue_runs: 1 });
    api.observeCoverPolicy.mockResolvedValue({ assessment: { gap: -0.4 }, rescue: { started: true, run_id: "r9", status: "WAITING_APPROVAL" } });
    api.settleCoverPolicy.mockResolvedValue({ status: "CLAIMED", claim: { payout_eur: 640, paid_units: 200 } });
    const user = userEvent.setup();
    render(<CoverPage role="admin" />);

    expect(await screen.findByTestId("cover-portfolio")).toHaveTextContent("€3,456");
    expect(screen.getByTestId("cover-ae")).toHaveTextContent("—"); // no experience block in this portfolio
    expect(screen.getByTestId("cover-policy-p2")).toHaveTextContent("last: 200 sold, -40% vs plan");
    await user.type(screen.getByTestId("cover-sold-p1"), "200");
    await user.type(screen.getByTestId("cover-onhand-p1"), "800");
    await user.click(screen.getByTestId("cover-observe-p1"));
    await waitFor(() => expect(api.observeCoverPolicy).toHaveBeenCalledWith("p1", { units_sold_to_date: 200, units_on_hand: 800 }));
    expect(await screen.findByTestId("cover-policy-message")).toHaveTextContent("rescue playbook started (run r9)");

    expect(screen.queryByTestId("cover-observe-p2")).not.toBeInTheDocument(); // window over: settle instead
    await user.type(screen.getByTestId("cover-unsold-p2"), "300");
    await user.click(screen.getByTestId("cover-settle-p2"));
    await waitFor(() => expect(api.settleCoverPolicy).toHaveBeenCalledWith("p2", { units_unsold: 300 }));
    expect(await screen.findByTestId("cover-policy-message")).toHaveTextContent("PO-8 settled: pays €640");
  });

  it("planners cannot settle", async () => {
    api.coverPolicies.mockResolvedValue({ policies: [policy({ effective_status: "AWAITING_SETTLEMENT" })] });
    render(<CoverPage role="planner" />);
    await screen.findByTestId("cover-policy-p1");
    expect(screen.queryByTestId("cover-settle-p1")).not.toBeInTheDocument();
  });

  it("quotes a PO from the platform forecast and shows the cap", async () => {
    api.coverQuote.mockResolvedValue(offered);
    api.coverQuotes.mockResolvedValueOnce({ quotes: [] }).mockResolvedValue({ quotes: [offered] });
    const user = userEvent.setup();
    render(<CoverPage role="planner" />);
    expect(screen.getByTestId("cover-submit")).toBeDisabled();
    await user.type(screen.getByTestId("cover-po_id"), "PO-7");
    await user.type(screen.getByTestId("cover-units"), "1500");
    await user.type(screen.getByTestId("cover-unit_landed_cost_eur"), "8");
    await user.type(screen.getByTestId("cover-item_id"), "42");
    await user.click(screen.getByTestId("cover-submit"));

    await waitFor(() => expect(api.coverQuote).toHaveBeenCalledWith({ po_id: "PO-7", sku: undefined, units: 1500,
      unit_landed_cost_eur: 8, item_id: 42 }));
    expect(await screen.findByTestId("cover-grade")).toHaveTextContent("B");
    expect(screen.getByTestId("cover-verdict")).toHaveTextContent("Cover offered: €182.4 (1.9% of covered value)");
    expect(screen.getByTestId("cover-result")).toHaveTextContent("290 above the cap stay with you");
    expect(screen.getByTestId("cover-recommended")).toHaveTextContent("1,210");
    expect(screen.getByTestId("cover-experience")).toHaveTextContent("Experience factor ×1.12 from 3 settled policies (actual/expected 1.4, credibility 32%)");
    expect(await screen.findByTestId("cover-quotes")).toHaveTextContent("PO-7");
  });

  it("uses manual demand and shows a decline", async () => {
    api.coverQuote.mockResolvedValue({ ...offered, offered: false, risk_grade: "E", premium_eur: null, premium_rate: null,
      decline_reasons: ["expected unsold share 52% is above the 35% limit; order less"] });
    const user = userEvent.setup();
    render(<CoverPage role="admin" />);
    await user.click(screen.getByTestId("cover-source-manual"));
    await user.type(screen.getByTestId("cover-po_id"), "PO-8");
    await user.type(screen.getByTestId("cover-units"), "3000");
    await user.type(screen.getByTestId("cover-unit_landed_cost_eur"), "8");
    await user.type(screen.getByTestId("cover-demand_mean_units"), "1000");
    await user.click(screen.getByTestId("cover-submit"));
    await waitFor(() => expect(api.coverQuote).toHaveBeenCalledWith(expect.objectContaining({
      po_id: "PO-8", demand_mean_units: 1000, demand_sd_units: undefined })));
    expect(api.coverQuote.mock.calls[0][0].item_id).toBeUndefined();
    expect(await screen.findByTestId("cover-verdict")).toHaveTextContent("Not offered");
    expect(screen.getByTestId("cover-result")).toHaveTextContent("order less");
  });

  it("explains a market-benchmark price for a seller without history", async () => {
    api.coverQuote.mockResolvedValue({ ...offered, experience: { policies: 0, a_to_e: null, credibility: 0, factor: 1.6,
      complement_source: "pooled", pooled_tenants: 4 } });
    const user = userEvent.setup();
    render(<CoverPage role="planner" />);
    await user.type(screen.getByTestId("cover-po_id"), "PO-1");
    await user.type(screen.getByTestId("cover-units"), "10");
    await user.type(screen.getByTestId("cover-unit_landed_cost_eur"), "1");
    await user.type(screen.getByTestId("cover-item_id"), "1");
    await user.click(screen.getByTestId("cover-submit"));
    expect(await screen.findByTestId("cover-experience"))
      .toHaveTextContent("No settled policies yet: priced on the market benchmark of 4 companies (×1.6).");
  });

  it("auditors see quotes but cannot request one", async () => {
    api.coverQuotes.mockResolvedValue({ quotes: [offered] });
    render(<CoverPage role="auditor" />);
    expect(await screen.findByTestId("cover-quotes")).toHaveTextContent("PO-7");
    expect(screen.queryByTestId("cover-submit")).not.toBeInTheDocument();
  });

  it("shows API errors", async () => {
    api.coverQuote.mockRejectedValue(new Error("item 42 not found"));
    const user = userEvent.setup();
    render(<CoverPage role="planner" />);
    await user.type(screen.getByTestId("cover-po_id"), "PO-9");
    await user.type(screen.getByTestId("cover-units"), "10");
    await user.type(screen.getByTestId("cover-unit_landed_cost_eur"), "1");
    await user.click(screen.getByTestId("cover-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("item 42 not found");
  });
});

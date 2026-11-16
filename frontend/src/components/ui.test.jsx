import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DataTable } from "./ui";

const columns = [{ key: "id", label: "ID" }];
const million = Array.from({ length: 1_000_000 }, (_, i) => ({ id: i }));

describe("DataTable windowing", () => {
  it("renders a bounded number of rows for a million-row set", () => {
    render(<DataTable columns={columns} rows={million} testId="grid" />);
    const body = screen.getByTestId("grid").querySelector("tbody");
    expect(body.querySelectorAll("tr").length).toBeLessThan(40);
  });

  it("reaches the final row when scrolled to the bottom", () => {
    render(<DataTable columns={columns} rows={million} testId="grid" />);
    fireEvent.scroll(screen.getByTestId("grid"), { target: { scrollTop: 1_000_000 * 32 } });
    expect(screen.getByTestId("grid")).toHaveTextContent("999999");
  });

  it("renders every row when the set fits the viewport", () => {
    render(<DataTable columns={columns} rows={[{ id: 1 }, { id: 2 }]} testId="small" />);
    expect(screen.getByTestId("small").querySelectorAll("tbody tr").length).toBe(2);
  });

  it("keeps the empty state", () => {
    render(<DataTable columns={columns} rows={[]} testId="none" empty="Nothing here." />);
    expect(screen.getByTestId("none")).toHaveTextContent("Nothing here.");
  });
});

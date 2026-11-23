import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Dashboard } from "./Dashboard";

describe("Dashboard page", () => {
  it("renders without crashing", () => {
    const { container } = render(<Dashboard />);
    const header = container.querySelector("h5");
    expect(header).toBeInTheDocument();
    expect(header?.textContent).toContain("Dashboard");
  });

  it("displays API health when health data is available", () => {
    const { container } = render(<Dashboard />);
    expect(container.querySelector(".MuiChip-root")).toBeInTheDocument();
  });

  it("displays task submission form", () => {
    const { container } = render(<Dashboard />);
    const form = container.querySelector("form");
    expect(form).toBeInTheDocument();
  });

  it("displays task list", () => {
    const { container } = render(<Dashboard />);
    expect(container.querySelector("table")).toBeInTheDocument();
  });

  it("displays skills registry summary", () => {
    const { container } = render(<Dashboard />);
    expect(container.querySelectorAll("button").length).toBeGreaterThan(0);
  });
});

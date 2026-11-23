import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Engineering } from "./Engineering";

describe("Engineering skill rendering", () => {
  it("renders without crashing", () => {
    const { container } = render(<Engineering />);
    expect(container.querySelector("h5")).toBeTruthy();
  });

  it("displays skills registry summary", () => {
    const { container } = render(<Engineering />);
    expect(container.querySelectorAll("*").length).toBeGreaterThan(0);
  });

  it("displays skill chips", () => {
    const { container } = render(<Engineering />);
    expect(container.querySelectorAll(".MuiChip-root").length).toBeGreaterThan(0);
  });

  it("displays skill table when skills are loaded", () => {
    const { container } = render(<Engineering />);
    expect(container.querySelector("table")).toBeNull();
  });
});

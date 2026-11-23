import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Tasks } from "./Tasks";

describe("Tasks page", () => {
  it("renders without crashing", () => {
    const { container } = render(<Tasks />);
    expect(container.querySelector("h5")).toBeTruthy();
  });

  it("displays task list", () => {
    const { container } = render(<Tasks />);
    expect(container.querySelector("table")).toBeTruthy();
  });

  it("displays refresh button", () => {
    const { container } = render(<Tasks />);
    expect(container.querySelector("button[variant='contained']")).toBeTruthy();
  });

  it("shows no tasks message when no tasks exist", () => {
    const { container } = render(<Tasks />);
    expect(container.querySelector("Typography")).toBeTruthy();
  });
});

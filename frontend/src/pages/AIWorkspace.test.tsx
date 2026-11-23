import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { AIWorkspace } from "./AIWorkspace";

describe("AIWorkspace component", () => {
  it("renders without crashing", () => {
    const { container } = render(<AIWorkspace />);
    const header = container.querySelector("h5");
    expect(header).toBeInTheDocument();
  });

  it("renders task submission form", () => {
    const { container } = render(<AIWorkspace />);
    const form = container.querySelector("form");
    expect(form).toBeInTheDocument();
  });

  it("renders submission result section", () => {
    const { container } = render(<AIWorkspace />);
    const resultSection = container.querySelector('[sx*="mt: 3"]');
    expect(resultSection).toBeInTheDocument();
  });

  it("renders task history section", () => {
    const { container } = render(<AIWorkspace />);
    const historySection = container.querySelector("Typography[component='div']");
    expect(historySection).toBeInTheDocument();
  });

  it("renders task list", () => {
    const { container } = render(<AIWorkspace />);
    const taskList = container.querySelector(".MuiTable-root");
    expect(taskList).toBeInTheDocument();
  });
});

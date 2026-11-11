import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AnswerVisual from "./AnswerVisual";

vi.mock("@/components/charts/ChartView", () => ({
  default: ({ spec }: { spec: { type: string } }) => <div data-testid="chart-view">{spec.type}</div>
}));

const COLUMNS = ["区县", "3月均分", "5月均分"];
const ROWS = [
  ["市直", 88.4, 86.1],
  ["广陵", 82.1, 79.6],
  ["邗江", 79.8, 77.2],
  ["江都", 76.5, 74.8],
  ["宝应", 72.3, 70.1],
  ["高邮", 69.8, 67.4],
  ["仪征", 66.2, 64.9]
];

describe("AnswerVisual", () => {
  it("渲染 KPI、主图、辅图与明细表", () => {
    render(
      <AnswerVisual
        question="对比各区县 3 月与 5 月数学均分"
        columns={COLUMNS}
        rows={ROWS}
        rowCount={49}
      />
    );
    expect(screen.getByText("数据洞察")).toBeInTheDocument();
    expect(screen.getAllByTestId("chart-view").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("主图")).toBeInTheDocument();
    expect(screen.getByText("辅图")).toBeInTheDocument();
    expect(screen.getByText("明细数据")).toBeInTheDocument();
    expect(screen.getByText(/已返回 7 行/)).toBeInTheDocument();
  });

  it("无数据不渲染", () => {
    const { container } = render(
      <AnswerVisual question="查询" columns={COLUMNS} rows={[]} />
    );
    expect(container).toBeEmptyDOMElement();
  });
});

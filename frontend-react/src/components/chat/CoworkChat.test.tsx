import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import CoworkChat from "./CoworkChat";
import { EMPTY_RUN_METRICS } from "@/utils/runMetrics";
import type { QueryResult } from "@/hooks/useChat";

vi.mock("@/api/auth", () => ({ getCurrentUser: () => new Promise(() => {}) }));
vi.mock("@/components/chat/DatasourcePicker", () => ({ default: () => null }));
vi.mock("@/components/chat/ChatExecutionPanel", () => ({
  default: ({ queryResults }: { queryResults: QueryResult[] }) => <p>{queryResults.map((result) => result.sql).join(", ")}</p>
}));

function chatState(overrides: Partial<Parameters<typeof CoworkChat>[0]["chat"]> = {}): Parameters<typeof CoworkChat>[0]["chat"] {
  return {
    messages: [], loading: false, activity: "", send: vi.fn(), stop: vi.fn(), datasourceId: 4,
    setDatasourceId: vi.fn(), conversationId: undefined, conversationTitle: "",
    reports: [], queryResults: [], clarifyByRunId: {}, runMetrics: EMPTY_RUN_METRICS,
    chartByRunId: {}, patchReport: vi.fn(), executionSteps: [], metricsByRunId: {}, taskPlanByRunId: {},
    thinkByRunId: {}, ...overrides,
  };
}

it("keeps previously saved tool protocol out of the answer and execution log", () => {
  const raw = "]<]minimax[>[<tool_call> ]<]minimax[>[<invoke name=\"query_semantic\">]<]minimax[>[<sql>SELECT private_field FROM exam_facts]<]minimax[>[</sql>]<]minimax[>[</invoke> ]<]minimax[>[</tool_call>";
  render(<CoworkChat chat={chatState({
    messages: [
      { id: "q", runId: "r", role: "user", content: "分析成绩" },
      { id: "a", runId: "r", role: "assistant", content: raw },
    ],
    queryResults: [{ key: "r", runId: "r", sql: "", columns: [], rows: [], rowCount: 0 }],
    executionSteps: [{ id: "s", runId: "r", title: "分析思路", kind: "thought", detail: `已读取口径。${raw}`, status: "done" }],
  })} />);
  const article = within(screen.getByRole("article"));
  expect(article.getByText("这轮未生成分析结论，请查看已返回的查询结果。")).toBeVisible();
  fireEvent.click(article.getByText(/执行记录/).closest("summary")!);
  expect(article.getByText("已读取口径。")).toBeVisible();
  expect(screen.queryByText(/minimax|private_field/)).not.toBeInTheDocument();
});

it("shows the streamed thinking above the answer", () => {
  render(<CoworkChat chat={chatState({
    messages: [
      { id: "q", runId: "r", role: "user", content: "分析成绩" },
      { id: "a", runId: "r", role: "assistant", content: "本次数学均分 82.5 分。" },
    ],
    loading: true,
    thinkByRunId: { r: "先确认考试口径，再汇总均分。" },
  })} />);
  expect(screen.getByText("思考中")).toBeVisible();
  expect(screen.getByText("先确认考试口径，再汇总均分。")).toBeVisible();
});

it("keeps IME confirmation local and submits the composed message with the selected datasource", () => {
  const chat = chatState();
  render(<CoworkChat chat={chat} />);
  const input = screen.getByRole("textbox", { name: "输入消息" });
  fireEvent.compositionStart(input);
  fireEvent.change(input, { target: { value: "考试均分" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(chat.send).not.toHaveBeenCalled();
  fireEvent.compositionEnd(input);
  fireEvent.keyDown(input, { key: "Enter" });
  expect(chat.send).toHaveBeenCalledWith("考试均分", { datasourceId: 4 });
});

it("uses the live plan for the active stage and completed percentage", () => {
  const breakpoint = window.matchMedia;
  const media = vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    ...breakpoint(query), matches: query === "(min-width: 992px)",
  }));
  const chat = chatState({
    messages: [{ id: "q", runId: "r", role: "user", content: "分析成绩变化" }],
    loading: true,
    executionSteps: [
      { id: "1", runId: "r", title: "分析思路", kind: "thought", detail: "对齐批次后再比较均分。", status: "done" },
      { id: "2", runId: "r", title: "查询", toolName: "query_semantic", subTaskIndex: 1, status: "running" },
    ],
    taskPlanByRunId: { r: [
      { content: "确认考试批次", status: "completed" },
      { content: "比较各科均分", status: "in_progress" },
      { content: "汇总趋势", status: "pending" },
      { content: "说明统计口径", status: "pending" },
    ] },
  });
  try {
    const { rerender } = render(<CoworkChat chat={chat} />);
    const article = within(screen.getByRole("article"));
    const log = article.getByText(/执行记录/);
    expect(log.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(log.closest("summary")!);
    expect(article.getByText("对齐批次后再比较均分。")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("比较各科均分");
    const panel = within(screen.getByRole("complementary", { name: "任务进度" }));
    expect(panel.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    expect(panel.getByText("进行中")).toBeVisible();
    rerender(<CoworkChat chat={{ ...chat, loading: false }} />);
    expect(panel.queryByText("进行中")).not.toBeInTheDocument();
    expect(panel.getAllByText("未确认完成")).toHaveLength(3);
    expect(panel.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
  } finally {
    media.mockRestore();
  }
});

it("groups adjacent queries and shows only user-facing tool metadata", () => {
  render(<CoworkChat chat={chatState({
    messages: [{ id: "q", runId: "r", role: "user", content: "比较两次考试" }],
    executionSteps: [
      { id: "1", runId: "r", title: "查询", toolName: "query_semantic", status: "done", rowCount: 2, elapsedMs: 40, detail: "SELECT private_column FROM private_table" },
      { id: "2", runId: "r", title: "查询", toolName: "query_semantic", status: "error", detail: "WITH internal AS (...)" },
    ],
  })} />);
  const article = within(screen.getByRole("article"));
  fireEvent.click(article.getByText(/执行记录/).closest("summary")!);
  const group = article.getByText("查询数据 · 2 次调用").closest("details")!;
  expect(group).not.toHaveAttribute("open");
  fireEvent.click(group.querySelector("summary")!);
  expect(within(group).getByText("返回 2 行 · 40 毫秒")).toBeVisible();
  expect(within(group).getByText("未完成")).toBeVisible();
  expect(screen.queryByText(/SELECT private_column|WITH internal/)).not.toBeInTheDocument();
});

it("opens the clicked round's results and keeps the latest clarification available", () => {
  const chat = chatState({
    messages: [
      { id: "q1", runId: "first", role: "user", content: "第一问" },
      { id: "q2", runId: "second", role: "user", content: "第二问" },
    ],
    queryResults: ["first", "second"].map((runId) => ({ key: runId, sql: runId, columns: [], rows: [], rowCount: 0, runId })),
    clarifyByRunId: { second: { interrupt_id: "ask-exam", prompt: "请选择考试", options: ["期中考试"] } },
  });
  render(<CoworkChat chat={chat} />);
  const [firstRound] = screen.getAllByRole("article");
  fireEvent.click(within(firstRound).getByRole("button", { name: "查询结果 · 1 组" }));
  expect(screen.getByText("first")).toBeInTheDocument();
  expect(screen.queryByText("second")).not.toBeInTheDocument();
  fireEvent.click(within(screen.getByRole("dialog", { name: "结果预览" })).getByRole("button", { name: "Close" }));
  fireEvent.click(within(screen.getByRole("group", { name: "助手追问" })).getByRole("button", { name: "期中考试" }));
  expect(chat.send).toHaveBeenCalledWith("期中考试", { datasourceId: 4 });
});

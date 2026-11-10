import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

it("places the live task toolbar in the shell slot without duplicating the heading", () => {
  const chat = chatState({
    messages: [{ id: "q", runId: "r", role: "user", content: "分析成绩" }],
    conversationTitle: "班级横向对比",
  });
  const view = (title: string) => <><div id="chat-header-slot" data-testid="shell-toolbar" /><CoworkChat chat={{ ...chat, conversationTitle: title }} /></>;
  const { rerender } = render(view("班级横向对比"));
  expect(within(screen.getByTestId("shell-toolbar")).getByRole("heading", { name: "班级横向对比" })).toBeVisible();
  expect(screen.getAllByRole("heading", { name: "班级横向对比" })).toHaveLength(1);
  rerender(view("最新分析结果"));
  expect(within(screen.getByTestId("shell-toolbar")).getByRole("heading", { name: "最新分析结果" })).toBeVisible();
});

it("keeps usage out of the composer until requested and labels unknown capacity", async () => {
  const state = chatState({
    messages: [{ id: "q", runId: "r", role: "user", content: "继续" }],
    runMetrics: { ...EMPTY_RUN_METRICS, totalTokens: 50000, tokenKnown: true,
      contextUsage: { context_tokens: 2000, context_limit: 100000, context_estimated: false } }
  });
  const { rerender } = render(<CoworkChat chat={state} />);
  expect(screen.queryByText("上下文 2000 / 10万（2%）")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看用量与上下文" }));
  await screen.findByText("累计用量 5万 Token");
  await waitFor(() => expect(screen.getByText("上下文 2000 / 10万（2%）")).toBeVisible());
  rerender(<CoworkChat chat={{ ...state, runMetrics: { ...state.runMetrics,
    contextUsage: { context_tokens: 2000, context_limit: null, context_estimated: true } } }} />);
  await waitFor(() => expect(screen.getByText("上下文 ≈2000 / 容量未知")).toBeVisible());
});

it("folds streamed thinking when the run settles and lets the reader reopen it", () => {
  const chat = chatState({
    messages: [
      { id: "q", runId: "r", role: "user", content: "分析成绩" },
      { id: "a", runId: "r", role: "assistant", content: "数学均分为 82.5 分。" },
    ],
    loading: true, thinkByRunId: { r: "先核对考试口径。" },
  });
  const { rerender } = render(<CoworkChat chat={chat} />);
  expect(screen.getByText("先核对考试口径。")).toBeVisible();
  rerender(<CoworkChat chat={{ ...chat, loading: false }} />);
  expect(screen.queryByText("先核对考试口径。")).not.toBeInTheDocument();
  expect(screen.getByText("数学均分为 82.5 分。")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "思考过程" }));
  expect(screen.getByText("先核对考试口径。")).toBeVisible();
});

it("collapses only fully completed task panels and allows reopening them", () => {
  const breakpoint = window.matchMedia;
  const media = vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    ...breakpoint(query), matches: query === "(min-width: 992px)",
  }));
  const chat = chatState({
    messages: [{ id: "q", runId: "r", role: "user", content: "分析成绩" }], loading: true,
    taskPlanByRunId: { r: [{ content: "生成分析结论", status: "in_progress" }] },
  });
  try {
    const { rerender } = render(<CoworkChat chat={chat} />);
    expect(screen.getByRole("complementary", { name: "任务进度" })).toBeVisible();
    rerender(<CoworkChat chat={{ ...chat, loading: false,
      taskPlanByRunId: { r: [{ content: "生成分析结论", status: "completed" }] } }} />);
    expect(screen.queryByRole("complementary", { name: "任务进度" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开任务进度" }));
    expect(screen.getByRole("complementary", { name: "任务进度" })).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    fireEvent.click(screen.getByRole("button", { name: "折叠任务进度" }));
    rerender(<CoworkChat chat={{ ...chat, loading: true,
      messages: [...chat.messages, { id: "q2", runId: "next", role: "user", content: "继续分析" }],
      taskPlanByRunId: { next: [{ content: "读取数据", status: "in_progress" }] } }} />);
    expect(screen.getByRole("complementary", { name: "任务进度" })).toBeVisible();
  } finally { media.mockRestore(); }
});

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

it("opens the clicked round's results and keeps the latest clarification available", async () => {
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
  const form = within(screen.getByRole("form", { name: "补充信息表单" }));
  fireEvent.click(form.getByRole("radio", { name: "期中考试" }));
  expect(chat.send).not.toHaveBeenCalled();
  fireEvent.click(form.getByRole("button", { name: "提交并继续" }));
  expect(chat.send).toHaveBeenCalledWith("1. 请选择或自行填写：期中考试", { datasourceId: 4, resumeInterruptId: "ask-exam" });
  await waitFor(() => expect(form.getByRole("button", { name: "提交并继续" })).toBeEnabled());
});

it("keeps a waiting form available after an unsuccessful reply and preserves drafts on collapse", () => {
  const chat = chatState({
    messages: [
      { id: "q1", runId: "first", role: "user", content: "分析成绩" },
      { id: "q2", runId: "failed", role: "user", content: "未提交的回复" },
      { id: "a2", runId: "failed", role: "assistant", content: "请求失败：连接中断" },
    ],
    clarifyByRunId: { first: { interrupt_id: "ask-1", prompt: "哪次考试？", status: "pending" } },
  });
  const { rerender } = render(<CoworkChat chat={chat} />);
  fireEvent.change(screen.getByRole("textbox", { name: "你的回复" }), { target: { value: "期中" } });
  rerender(<CoworkChat chat={{ ...chat, loading: true }} />);
  expect(screen.queryByRole("form")).not.toBeInTheDocument();
  rerender(<CoworkChat chat={chat} />);
  expect(screen.getByRole("textbox", { name: "你的回复" })).toHaveValue("期中");
  fireEvent.click(screen.getByRole("button", { name: "收起" }));
  expect(screen.getByRole("textbox", { name: "回复追问" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "展开" }));
  expect(screen.getByRole("textbox", { name: "你的回复" })).toHaveValue("期中");
  expect(screen.getByText("请求失败：连接中断")).toBeVisible();
});

it("renders answered clarification history without reopening the form", () => {
  render(<CoworkChat chat={chatState({
    messages: [{ id: "q", runId: "r", role: "user", content: "分析成绩" }],
    clarifyByRunId: { r: { interrupt_id: "ask-1", prompt: "哪次考试？", status: "answered", answer: "期中考试" } },
  })} />);
  expect(screen.queryByRole("form")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("补充信息 · 已回复"));
  expect(screen.getByText("期中考试")).toBeVisible();
});

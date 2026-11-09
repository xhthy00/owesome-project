import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import HumanQuestionForm, { questionFields } from "./HumanQuestionForm";
import type { ClarifyPayload } from "@/api/adapter/chatAdapter";

const question: ClarifyPayload = { interrupt_id: "ask-1", prompt: "确认分析范围", fields: [
  { label: "考试", kind: "single", options: ["期中", "期末"], required: true },
  { label: "科目", kind: "multiple", options: ["语文", "数学"], required: true },
  { label: "补充要求", kind: "text", required: false },
] };

it("validates required fields, retains previous selections, and submits all answers once", async () => {
  const submit = vi.fn();
  render(<HumanQuestionForm question={question} onSubmit={submit} />);
  fireEvent.click(screen.getByRole("button", { name: "下一项" }));
  expect(screen.getByRole("alert")).toHaveTextContent("请回答这一项");
  expect(screen.getByRole("radio", { name: "期中" })).toHaveFocus();
  fireEvent.click(screen.getByRole("radio", { name: "期中" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "下一项" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "语文" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "数学" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "自己填写" }));
  fireEvent.click(screen.getByRole("button", { name: "下一项" }));
  expect(screen.getByRole("alert")).toHaveTextContent("请填写自定义内容");
  fireEvent.change(screen.getByRole("textbox", { name: "科目：自己填写" }), { target: { value: "英语" } });
  fireEvent.click(screen.getByRole("button", { name: "上一项" }));
  expect(screen.getByRole("radio", { name: "期中" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "下一项" }));
  expect(screen.getByRole("checkbox", { name: "数学" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "下一项" }));
  fireEvent.click(screen.getByRole("button", { name: "跳过此项" }));
  expect(submit).toHaveBeenCalledExactlyOnceWith("1. 考试：期中\n2. 科目：语文；数学；英语");
  await waitFor(() => expect(screen.getByRole("button", { name: "提交并继续" })).toBeEnabled());
});

it("supports custom single choices and changing back to a provided choice", async () => {
  const submit = vi.fn();
  render(<HumanQuestionForm question={{ fields: [question.fields![0]] }} onSubmit={submit} />);
  fireEvent.click(screen.getByRole("radio", { name: "自己填写" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "月考" } });
  fireEvent.click(screen.getByRole("radio", { name: "期末" }));
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "提交并继续" }));
  expect(submit).toHaveBeenCalledWith("1. 考试：期末");
  await waitFor(() => expect(screen.getByRole("button", { name: "提交并继续" })).toBeEnabled());
});

it("allows skipping every optional field and prevents duplicate requests while submitting", async () => {
  let complete!: () => void;
  const submit = vi.fn(() => new Promise<void>((resolve) => { complete = resolve; }));
  render(<HumanQuestionForm question={{ fields: [{ label: "偏好", kind: "text" }] }} onSubmit={submit} />);
  fireEvent.click(screen.getByRole("button", { name: "跳过此项" }));
  fireEvent.submit(screen.getByRole("form"));
  expect(submit).toHaveBeenCalledExactlyOnceWith("暂不补充，请根据已有信息继续处理，不确定的信息请标注待确认。");
  expect(screen.getByRole("button", { name: "正在提交…" })).toBeDisabled();
  complete();
  await waitFor(() => expect(screen.getByRole("button", { name: "提交并继续" })).toBeEnabled());
});

it("retains answers and allows retrying after a submission error", async () => {
  const submit = vi.fn().mockRejectedValueOnce(new Error("network")).mockResolvedValue(undefined);
  render(<HumanQuestionForm question={{ prompt: "请说明" }} onSubmit={submit} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "  补充内容  " } });
  fireEvent.click(screen.getByRole("button", { name: "提交并继续" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("提交失败");
  expect(screen.getByRole("textbox")).toHaveValue("  补充内容  ");
  fireEvent.click(screen.getByRole("button", { name: "提交并继续" }));
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
});

it("handles legacy fields, numbered questions, and choice fields without options", () => {
  expect(questionFields({ options: { exam: ["期中"] } })[0].kind).toBe("single");
  expect(questionFields({ prompt: "请确认\n1. 考试名称\n2. 分析范围" }).map((field) => field.label))
    .toEqual(["考试名称", "分析范围"]);
  render(<HumanQuestionForm question={{ fields: [{ label: "考试", kind: "single", required: true }] }} onSubmit={vi.fn()} />);
  expect(screen.getByRole("textbox", { name: "考试" })).toBeVisible();
});

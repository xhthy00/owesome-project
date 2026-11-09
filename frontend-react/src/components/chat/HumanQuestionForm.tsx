import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ClarifyPayload, HumanQuestionField } from "@/api/adapter/chatAdapter";

/** Same form contract and answer text as my-cowork's HumanQuestionCard. */
export function questionFields(question: ClarifyPayload): HumanQuestionField[] {
  if (question.fields?.length) return question.fields;
  const options = Array.isArray(question.options) ? question.options : Object.values(question.options ?? {}).flat();
  if (options.length) return [{ label: "请选择或自行填写", kind: "single", options, required: true }];
  // Compatibility with persisted, numbered questionnaires predating fields.
  const lines = (question.prompt ?? "").split("\n");
  const numbered = lines.flatMap((line) => {
    const match = line.match(/^\s*(\d+)[.、)）]\s*(.+)$/);
    return match ? [{ number: Number(match[1]), label: match[2] }] : [];
  });
  if (numbered.length > 1 && numbered.every((field, index) => field.number === index + 1)) {
    return numbered.map(({ label }) => ({ label, kind: "text", required: true }));
  }
  return [{ label: "你的回复", kind: "text", required: true }];
}

const SKIPPED_ANSWER = "暂不补充，请根据已有信息继续处理，不确定的信息请标注待确认。";

export default function HumanQuestionForm({ question, hidden = false, onSubmit }: {
  question: ClarifyPayload;
  hidden?: boolean;
  onSubmit: (answer: string) => void | Promise<void>;
}) {
  const fields = useMemo(() => questionFields(question), [question]);
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<Record<number, string[]>>({});
  const [custom, setCustom] = useState<Record<number, boolean>>({});
  const [text, setText] = useState<Record<number, string>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const busy = useRef(false);
  const body = useRef<HTMLFieldSetElement>(null);
  const id = useId();
  const field = fields[index];
  const choiceField = field.kind !== "text" && Boolean(field.options?.length);
  const last = index === fields.length - 1;

  useEffect(() => {
    if (!hidden) body.current?.querySelector<HTMLElement>("textarea, input")?.focus();
  }, [index, hidden]);

  const values = (i: number, skip = false) => skip ? [] : [
    ...(selected[i] ?? []),
    ...(fields[i].kind === "text" || !fields[i].options?.length || custom[i] ? [text[i]?.trim() ?? ""] : []),
  ].filter(Boolean);

  const clearError = () => { setErrors((prev) => ({ ...prev, [index]: "" })); setSubmitError(""); };

  const advance = async (skip = false) => {
    if (busy.current) return;
    const error = !skip && custom[index] && !text[index]?.trim() ? "请填写自定义内容"
      : !skip && field.required && !values(index).length ? "请回答这一项" : "";
    if (error) {
      setErrors((prev) => ({ ...prev, [index]: error }));
      body.current?.querySelector<HTMLElement>(custom[index] ? "textarea" : "textarea, input")?.focus();
      return;
    }
    if (skip) {
      setSelected((prev) => ({ ...prev, [index]: [] }));
      setText((prev) => ({ ...prev, [index]: "" }));
      setCustom((prev) => ({ ...prev, [index]: false }));
    }
    clearError();
    if (!last) { setIndex(index + 1); return; }
    const answer = fields.flatMap((item, i) => {
      const entries = values(i, skip && i === index);
      return entries.length ? [`${i + 1}. ${item.label}：${entries.join("；")}`] : [];
    }).join("\n") || SKIPPED_ANSWER;
    if (answer.length > 8000) { setSubmitError("回复内容过长，请将总长度缩减至 8000 字以内。"); return; }
    busy.current = true;
    setSubmitting(true);
    try { await onSubmit(answer); }
    catch { setSubmitError("提交失败，已保留填写内容，请重试。"); }
    finally { busy.current = false; setSubmitting(false); }
  };

  return (
    <form className="cowork-human-form" hidden={hidden} aria-label="补充信息表单" aria-busy={submitting}
      onSubmit={(event) => { event.preventDefault(); void advance(); }}>
      <p className="cowork-human-intro">{question.prompt}</p>
      <fieldset ref={body} disabled={submitting} aria-describedby={errors[index] ? `${id}-error` : undefined}>
        <legend>{field.label}<span className="cowork-human-required">{field.required ? "必填" : "选填"}</span></legend>
        {!choiceField ? (
          <textarea aria-label={field.label} aria-required={field.required} aria-invalid={Boolean(errors[index])}
            aria-describedby={errors[index] ? `${id}-error` : undefined}
            value={text[index] ?? ""} placeholder={field.placeholder || "填写你的回复…"} maxLength={8000} rows={3}
            onChange={(event) => { setText((prev) => ({ ...prev, [index]: event.target.value })); clearError(); }} />
        ) : <>
          <div className="cowork-human-options">
            {[...new Set(field.options ?? [])].map((option) => <label key={option}>
              <input type={field.kind === "multiple" ? "checkbox" : "radio"} name={`${id}-${index}`}
                checked={(selected[index] ?? []).includes(option)} onChange={() => {
                  setSelected((prev) => ({ ...prev, [index]: field.kind === "multiple"
                    ? (prev[index] ?? []).includes(option) ? (prev[index] ?? []).filter((v) => v !== option) : [...(prev[index] ?? []), option]
                    : [option] }));
                  if (field.kind === "single") setCustom((prev) => ({ ...prev, [index]: false }));
                  clearError();
                }} /><span>{option}</span>
            </label>)}
            <label>
              <input type={field.kind === "multiple" ? "checkbox" : "radio"} name={`${id}-${index}`}
                checked={Boolean(custom[index])} onChange={() => {
                  setCustom((prev) => ({ ...prev, [index]: !prev[index] }));
                  if (field.kind === "single") setSelected((prev) => ({ ...prev, [index]: [] }));
                  clearError();
                }} /><span>自己填写</span>
            </label>
          </div>
          {custom[index] && <textarea aria-label={`${field.label}：自己填写`} aria-invalid={Boolean(errors[index])}
            aria-describedby={errors[index] ? `${id}-error` : undefined}
            value={text[index] ?? ""} rows={2} maxLength={8000} placeholder={field.placeholder || "填写其他内容…"}
            onChange={(event) => { setText((prev) => ({ ...prev, [index]: event.target.value })); clearError(); }} />}
        </>}
        {errors[index] && <p id={`${id}-error`} className="cowork-human-error" role="alert">{errors[index]}</p>}
      </fieldset>
      {submitError && <p className="cowork-human-error" role="alert">{submitError}</p>}
      <div className="cowork-human-actions">
        <button type="button" disabled={index === 0 || submitting} onClick={() => { clearError(); setIndex(index - 1); }}>上一项</button>
        <span aria-live="polite">{index + 1} / {fields.length}</span>
        <div>
          {!field.required && <button type="button" disabled={submitting} onClick={() => void advance(true)}>跳过此项</button>}
          <button type="submit" className="cowork-human-submit" disabled={submitting}>
            {submitting ? "正在提交…" : last ? "提交并继续" : "下一项"}
          </button>
        </div>
      </div>
    </form>
  );
}

export function HumanQuestionSummary({ question }: { question: ClarifyPayload }) {
  return <details className="cowork-human-summary">
    <summary>补充信息 · {question.status === "cancelled" ? "已结束追问" : "已回复"}</summary>
    <p>{question.prompt}</p>
    {question.answer && <p>{question.answer}</p>}
  </details>;
}

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Select } from "antd";
import type { ClarifyPayload, HumanQuestionField } from "@/api/adapter/chatAdapter";

/** 模型经常把候选写进字段名或问题里，这里统一抽成可点选的选项。 */
const MARKER_SOURCE =
  "(?:(?:候选(?:项|学校|批次|范围)?|可选项?|选项|如下)(?:[是为有含]|包括|含有)?\\s*[：:]?" +
  "|(?:包括|例如|比如|如)\\s*[：:])\\s*";
const CLAUSE_SPLIT = /[、,，;；/\n]/;
const PARENTHETICAL = /^([^（(]+)[（(]([\s\S]*)[）)]\s*$/;
const LABEL_LIMIT = 40;
const OPTION_LIMIT = 30;
const MAX_OPTIONS = 200;

const CUSTOM_LABEL = "自己填写";

function cleanOption(value: string): string {
  return value.trim().replace(/^[（(]+|[）)]+$/g, "").replace(/[。；;，,、]+$/, "").slice(0, OPTION_LIMIT);
}

function dedupe(values: string[]): string[] {
  const seen = new Set<string>();
  const options: string[] = [];
  for (const value of values) {
    const text = cleanOption(value);
    if (!text || seen.has(text)) continue;
    seen.add(text);
    options.push(text);
    if (options.length >= MAX_OPTIONS) break;
  }
  return options;
}

function splitCandidates(raw: string): string[] {
  const items = raw.split(CLAUSE_SPLIT).map(cleanOption).filter(Boolean);
  return items.length > 1 ? dedupe(items) : [];
}

/** 从“候选含 A、B、C”这类文案里抽出清单；抽不到返回空数组。 */
export function extractCandidates(text: string): string[] {
  if (!text) return [];
  const pattern = new RegExp(MARKER_SOURCE, "g");
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text))) {
    const tail = text.slice(match.index + match[0].length).split(/[。\n]/)[0].trim();
    const options = splitCandidates(tail);
    if (options.length) return options;
    if (pattern.lastIndex <= match.index) pattern.lastIndex = match.index + 1;
  }
  return [];
}

/** 去掉文案里的候选清单，只留下字段名/问题本身。 */
export function stripCandidates(text: string): string {
  const value = text.trim();
  if (!value) return value;
  const parenthetical = value.match(PARENTHETICAL);
  if (parenthetical && extractCandidates(parenthetical[2]).length) return parenthetical[1].trim();
  const match = new RegExp(MARKER_SOURCE).exec(value);
  const head = (match ? value.slice(0, match.index) : value).replace(/[（(，,、;；:：\s]+$/, "").trim();
  return head || value;
}

function shortenLabel(text: string): string {
  const label = text.replace(/\s+/g, " ").trim();
  if (label.length <= LABEL_LIMIT) return label;
  for (const separator of ["，", ",", "；", ";", "、", "（", "("]) {
    const index = label.indexOf(separator);
    if (index > 0 && index <= LABEL_LIMIT) return label.slice(0, index).trim();
  }
  return `${label.slice(0, LABEL_LIMIT - 1).trim()}…`;
}

function normalizeField(field: HumanQuestionField, promptCandidates: string[], solo: boolean): HumanQuestionField {
  const fromLabel = extractCandidates(field.label);
  const options = dedupe([...(field.options ?? []), ...fromLabel, ...(fromLabel.length || field.options?.length ? [] : solo ? promptCandidates : [])]);
  const label = options.length ? shortenLabel(stripCandidates(field.label)) : shortenLabel(field.label);
  const hasOptions = options.length > 0;
  return {
    ...field,
    label: label || "请补充信息",
    kind: hasOptions ? (field.kind === "multiple" ? "multiple" : "single") : "text",
    options,
  };
}

export function questionFields(question: ClarifyPayload): HumanQuestionField[] {
  const promptCandidates = extractCandidates(question.prompt ?? "");
  const raw: HumanQuestionField[] = question.fields?.length
    ? question.fields
    : Array.isArray(question.options) && question.options.length
      ? [{ label: "请选择", kind: "single", options: question.options, required: true }]
      : question.options && !Array.isArray(question.options)
        ? Object.entries(question.options)
            .filter(([, options]) => options.length)
            .map(([label, options]) => ({ label, kind: "single" as const, options, required: true }))
        : [];
  if (raw.length) return raw.map((field) => normalizeField(field, promptCandidates, raw.length === 1));
  // Compatibility with persisted, numbered questionnaires predating fields.
  const lines = (question.prompt ?? "").split("\n");
  const numbered = lines.flatMap((line) => {
    const match = line.match(/^\s*(\d+)[.、)）]\s*(.+)$/);
    return match ? [{ number: Number(match[1]), label: match[2] }] : [];
  });
  if (numbered.length > 1 && numbered.every((field, index) => field.number === index + 1)) {
    return numbered.map(({ label }) => normalizeField({ label, kind: "text", required: true }, promptCandidates, false));
  }
  return [normalizeField({ label: "你的回复", kind: "text", required: true }, promptCandidates, true)];
}

/** 问题本身：候选清单已抽成选项时，不再把这四十个学校重复显示一遍。 */
export function questionIntro(question: ClarifyPayload): string {
  const prompt = (question.prompt ?? "").trim();
  if (!prompt) return "";
  const hasCandidates = extractCandidates(prompt).length > 0
    || (question.fields ?? []).some((field) => extractCandidates(field.label).length > 0);
  return hasCandidates ? stripCandidates(prompt) : prompt;
}

const SKIPPED_ANSWER = "暂不补充，请根据已有信息继续处理，不确定的信息请标注待确认。";

export default function HumanQuestionForm({ question, hidden = false, onSubmit }: {
  question: ClarifyPayload;
  hidden?: boolean;
  onSubmit: (answer: string) => void | Promise<void>;
}) {
  const fields = useMemo(() => questionFields(question), [question]);
  const intro = useMemo(() => questionIntro(question), [question]);
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
  const multiple = field.kind === "multiple";
  const dropdown = choiceField && (field.options?.length ?? 0) > 8;
  const last = index === fields.length - 1;

  useEffect(() => {
    if (!hidden) body.current?.querySelector<HTMLElement>("textarea, input")?.focus();
  }, [index, hidden]);

  const customOn = Boolean(custom[index]);
  const values = (i: number, skip = false) => skip ? [] : [
    ...(selected[i] ?? []),
    ...(fields[i].kind === "text" || custom[i] ? [text[i]?.trim() ?? ""] : []),
  ].filter(Boolean);

  const clearError = () => { setErrors((prev) => ({ ...prev, [index]: "" })); setSubmitError(""); };

  const toggleCustom = () => {
    setCustom((prev) => ({ ...prev, [index]: !prev[index] }));
    if (!multiple) setSelected((prev) => ({ ...prev, [index]: [] }));
    clearError();
  };

  const advance = async (skip = false) => {
    if (busy.current) return;
    const error = skip ? ""
      : customOn && !text[index]?.trim() ? "请填写自定义内容"
      : field.required && !values(index).length ? (choiceField ? "请选择一项" : "请回答这一项")
      : "";
    if (error) {
      setErrors((prev) => ({ ...prev, [index]: error }));
      body.current?.querySelector<HTMLElement>(customOn ? "textarea" : "textarea, input")?.focus();
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

  const choose = (option: string) => {
    setSelected((prev) => ({ ...prev, [index]: multiple
      ? (prev[index] ?? []).includes(option) ? (prev[index] ?? []).filter((value) => value !== option) : [...(prev[index] ?? []), option]
      : [option] }));
    if (!multiple) setCustom((prev) => ({ ...prev, [index]: false }));
    clearError();
  };

  const customRow = (
    <label className={`cowork-human-custom${customOn ? " is-on" : ""}`}>
      <input type={multiple ? "checkbox" : "radio"} name={`${id}-${index}`} checked={customOn}
        onChange={toggleCustom} />
      <span>{CUSTOM_LABEL}</span>
    </label>
  );

  return (
    <form className="cowork-human-form" hidden={hidden} aria-label="补充信息表单" aria-busy={submitting}
      onSubmit={(event) => { event.preventDefault(); void advance(); }}>
      {intro && <p className="cowork-human-intro">{intro}</p>}
      <fieldset ref={body} disabled={submitting} aria-describedby={errors[index] ? `${id}-error` : undefined}>
        <legend>{field.label}<span className="cowork-human-required">{field.required ? "必填" : "选填"}</span></legend>
        {!choiceField ? (
          <textarea aria-label={field.label} aria-required={field.required} aria-invalid={Boolean(errors[index])}
            aria-describedby={errors[index] ? `${id}-error` : undefined}
            value={text[index] ?? ""} placeholder={field.placeholder || "填写你的回复…"} maxLength={8000} rows={3}
            onChange={(event) => { setText((prev) => ({ ...prev, [index]: event.target.value })); clearError(); }} />
        ) : dropdown ? <>
          <Select
            key={index}
            aria-label={field.label}
            aria-required={field.required}
            aria-describedby={errors[index] ? `${id}-error` : undefined}
            status={errors[index] ? "error" : undefined}
            mode={multiple ? "multiple" : undefined}
            showSearch={{ optionFilterProp: "label" }}
            placeholder={`请选择${field.label}（共 ${field.options?.length} 项，可搜索）`}
            value={multiple ? selected[index] ?? [] : selected[index]?.[0]}
            options={field.options?.map((option) => ({ label: option, value: option }))}
            style={{ width: "100%" }}
            onChange={(value: string | string[]) => {
              setSelected((prev) => ({ ...prev, [index]: Array.isArray(value) ? value : [value] }));
              clearError();
            }}
          />
          {customRow}
          {customOn && <textarea aria-label={`${field.label}：${CUSTOM_LABEL}`} aria-invalid={Boolean(errors[index])}
            aria-describedby={errors[index] ? `${id}-error` : undefined}
            value={text[index] ?? ""} rows={2} maxLength={8000} placeholder={field.placeholder || "填写其他内容…"}
            onChange={(event) => { setText((prev) => ({ ...prev, [index]: event.target.value })); clearError(); }} />}
        </> : <>
          <div className="cowork-human-options" role={multiple ? "group" : "radiogroup"} aria-label={field.label}>
            {(field.options ?? []).map((option) => <label key={option}>
              <input type={multiple ? "checkbox" : "radio"} name={`${id}-${index}`}
                checked={(selected[index] ?? []).includes(option)} onChange={() => choose(option)} /><span>{option}</span>
            </label>)}
            {customRow}
          </div>
          {customOn && <textarea aria-label={`${field.label}：${CUSTOM_LABEL}`} aria-invalid={Boolean(errors[index])}
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
    <p>{questionIntro(question)}</p>
    {question.answer && <p>{question.answer}</p>}
  </details>;
}

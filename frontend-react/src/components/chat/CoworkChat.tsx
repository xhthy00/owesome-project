import {
  ApartmentOutlined, ArrowDownOutlined, ArrowUpOutlined, BulbOutlined, CheckOutlined, CloseCircleOutlined, CopyOutlined,
  DatabaseOutlined, EyeOutlined, FileTextOutlined, InfoCircleOutlined, LeftOutlined, MessageOutlined,
  PlusOutlined, QuestionCircleOutlined, RightOutlined, RobotOutlined,
} from "@ant-design/icons";
import { Drawer, Grid, Popover, message as toast } from "antd";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getCurrentUser } from "@/api/auth";
import AnswerVisual from "@/components/chat/AnswerVisual";
import ChatExecutionPanel from "@/components/chat/ChatExecutionPanel";
import { ChatComposerStatus, ChatTaskProgress, ChatWorkLog, runActivity } from "@/components/chat/ChatRunProgress";
import DatasourcePicker from "@/components/chat/DatasourcePicker";
import HumanQuestionForm, { HumanQuestionSummary } from "@/components/chat/HumanQuestionForm";
import type { Message, useChat } from "@/hooks/useChat";
import { ASSISTANT_AVATAR, ASSISTANT_WELCOME_TITLE, getAssistantSuggestions } from "@/new-components/chat/assistant";
import { eduMarkdownComponents } from "@/new-components/chat/content/eduMarkdownComponents";
import { snippetConversationTitle } from "@/utils/conversationTitle";
import { formatContextUsage, formatTokenCount } from "@/utils/runMetrics";
import { extractThinkFromSteps, extractThinkFromText, mergeThinkText, splitThinkContent } from "@/utils/toolLabels";

const markdownPlugins = [remarkGfm];

type ChatViewState = Pick<ReturnType<typeof useChat>,
  "messages" | "loading" | "send" | "stop" | "datasourceId" | "setDatasourceId" | "conversationId" |
  "conversationTitle" | "reports" | "queryResults" | "clarifyByRunId" | "runMetrics" | "chartByRunId" |
  "patchReport" | "executionSteps" | "metricsByRunId" | "activity" | "taskPlanByRunId" | "thinkByRunId"
>;

function visibleMessage(item: Message) {
  return item.role === "assistant" ? splitThinkContent(item.content).plain : item.content;
}

/** 流式期间默认展开，结束后收起；用户仍可展开回看。 */
function ThinkBlock({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(live);
  const body = useRef<HTMLPreElement>(null);
  useEffect(() => { setOpen(live); }, [live]);
  useEffect(() => {
    if (open && body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [text, open]);
  return (
    <div className={`cowork-think ${live ? "cowork-think-live" : ""}`}>
      <button type="button" className="cowork-think-toggle" aria-expanded={open}
        title={open ? "折叠思考过程" : "展开思考过程"} onClick={() => setOpen((value) => !value)}>
        <BulbOutlined aria-hidden="true" />
        <span>{live ? "思考中" : "思考过程"}</span>
        <ArrowDownOutlined className={`cowork-think-caret ${open ? "is-open" : ""}`} aria-hidden="true" />
      </button>
      {open && <pre ref={body} className="cowork-think-body">{text}</pre>}
    </div>
  );
}

function CopyRow({ item }: { item: Message }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);
  const date = item.createdAt != null ? new Date(item.createdAt) : undefined;
  const time = date && Number.isFinite(date.getTime()) ? date : undefined;

  return (
    <div className={`cowork-copy ${item.role === "user" ? "cowork-copy-right" : ""}`}>
      {time && <time dateTime={time.toISOString()} title={time.toLocaleString("zh-CN")}>
        {time.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}
      </time>}
      <button type="button" className="cowork-icon" title="复制消息" aria-label={copied ? "已复制" : "复制消息"}
        onClick={async () => {
          try { await navigator.clipboard.writeText(visibleMessage(item)); setCopied(true); }
          catch { void toast.error("复制失败"); }
        }}>
        {copied ? <CheckOutlined /> : <CopyOutlined />}
      </button>
    </div>
  );
}

export default function CoworkChat({ chat }: { chat: ChatViewState }) {
  const { messages, loading, send, stop, datasourceId, setDatasourceId, conversationId } = chat;
  const users = messages.filter((item) => item.role === "user");
  const welcome = users.length === 0;
  const [input, setInput] = useState("");
  const [suggestions, setSuggestions] = useState<readonly string[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [preview, setPreview] = useState<{ runId: string; kind: "report" | "data" | "all" }>();
  const [progressOpen, setProgressOpen] = useState(true);
  const [mobileProgressOpen, setMobileProgressOpen] = useState(false);
  const [questionCollapsed, setQuestionCollapsed] = useState(false);
  const [showBottom, setShowBottom] = useState(false);
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const nearBottom = useRef(true);
  const lastScrollRun = useRef<string>();
  const wasLoading = useRef(false);
  const composing = useRef(false);
  const { lg } = Grid.useBreakpoint();

  useEffect(() => { setHeaderSlot(document.getElementById("chat-header-slot")); }, []);

  useEffect(() => {
    if (!editor.current) return;
    editor.current.style.height = "auto";
    editor.current.style.height = `${Math.min(editor.current.scrollHeight, 180)}px`;
  }, [input, welcome, questionCollapsed]);

  useEffect(() => {
    let current = true;
    void getCurrentUser().then((user) => {
      if (current) setSuggestions(getAssistantSuggestions(user.edu_scope?.edu_role));
    }).catch(() => { if (current) setSuggestions(getAssistantSuggestions()); });
    return () => { current = false; };
  }, []);

  const latestRun = users[users.length - 1]?.runId;
  const latestSteps = chat.executionSteps.filter((step) => step.runId === latestRun);
  const stages = latestRun ? chat.taskPlanByRunId[latestRun] ?? [] : [];
  const activeStage = stages.find((stage) => stage.status === "in_progress");
  const activity = runActivity(latestSteps, chat.activity);
  const pending = users.slice().reverse().map((user) => chat.clarifyByRunId[user.runId || ""])
    .find((question) => question && question.status !== "answered" && question.status !== "cancelled");
  const latestArtifacts = [...chat.reports, ...chat.queryResults].some((item) => item.runId === latestRun);
  const activePreview = preview && users.some((item) => item.runId === preview.runId) ? preview : undefined;
  const planComplete = stages.length > 0 && stages.every((stage) => stage.status === "completed");
  const waiting = Boolean(pending) && !loading;
  const contextLabel = formatContextUsage(chat.runMetrics.contextUsage);
  const showUsage = !welcome && (chat.runMetrics.tokenKnown || Boolean(contextLabel));

  useEffect(() => {
    setQuestionCollapsed(false);
    nearBottom.current = true;
    setShowBottom(false);
    setProgressOpen(true);
  }, [latestRun]);

  useEffect(() => {
    if (planComplete && !loading && !waiting) setProgressOpen(false);
  }, [latestRun, planComplete, loading, waiting]);

  useEffect(() => {
    if (welcome) { setInput(""); setPreview(undefined); setMobileProgressOpen(false); }
  }, [welcome]);

  useEffect(() => {
    const firstSettledView = latestRun !== lastScrollRun.current && !loading;
    const justFinished = wasLoading.current && !loading;
    if (nearBottom.current && viewport.current) {
      const pane = viewport.current;
      const answer = pane.querySelector<HTMLElement>(".cowork-turn:last-child .cowork-answer");
      if ((firstSettledView || justFinished) && answer) {
        pane.scrollTop += answer.getBoundingClientRect().top - pane.getBoundingClientRect().top - 24;
        nearBottom.current = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 160;
        setShowBottom(!nearBottom.current);
      } else {
        pane.scrollTop = pane.scrollHeight;
      }
    }
    lastScrollRun.current = latestRun;
    wasLoading.current = loading;
  }, [latestRun, messages, chat.executionSteps, chat.reports, chat.queryResults, loading]);

  const submit = (value = input) => {
    if (!value.trim() || loading) return;
    setInput("");
    void send(value.trim(), { datasourceId, ...(pending?.interrupt_id ? { resumeInterruptId: pending.interrupt_id } : {}) });
  };

  const composer = (
    <div className={`cowork-composer ${welcome ? "cowork-composer-welcome" : ""}`}>
      <div className="cowork-editor">
        <textarea ref={editor} value={input} onChange={(event) => setInput(event.target.value)}
          onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !composing.current && !event.nativeEvent.isComposing) {
              event.preventDefault(); submit();
            }
          }}
          rows={welcome ? 3 : 1} maxLength={pending ? 8000 : 1000} disabled={loading} aria-label={pending ? "回复追问" : "输入消息"}
          placeholder={pending ? "回复助手的问题…" : loading ? "任务进行中，完成后可继续追问…" : welcome ? "今天帮你分析些什么？" : "继续追问…"} />
        <div className="cowork-composer-toolbar">
          <Popover trigger="click" placement="topLeft" open={menuOpen && !loading && !pending} onOpenChange={setMenuOpen}
            content={<div className="cowork-examples">
              <strong>提问示例</strong>
              {suggestions.map((text) => <button key={text} type="button" onClick={() => {
                setInput(text); setMenuOpen(false); editor.current?.focus();
              }}><MessageOutlined aria-hidden="true" />{text}</button>)}
            </div>}>
            <button type="button" className="cowork-icon" disabled={loading || Boolean(pending)}
              title="提问示例" aria-label="提问示例" aria-expanded={menuOpen && !loading && !pending}><PlusOutlined /></button>
          </Popover>
          <span className="cowork-input-hint">Enter 发送 · Shift + Enter 换行</span>
          <button type="button" className={`cowork-send ${loading ? "cowork-stop" : ""}`} disabled={!loading && !input.trim()}
            title={loading ? "停止任务" : "发送消息"} aria-label={loading ? "停止任务" : "发送消息"}
            onClick={() => loading ? stop() : submit()}>
            {loading ? <span className="cowork-stop-mark" aria-hidden="true" /> : <ArrowUpOutlined />}
          </button>
        </div>
      </div>
      <div className="cowork-composer-footer">
        <DatasourcePicker value={datasourceId} onChange={setDatasourceId} compact disabled={loading || Boolean(conversationId)} />
        <span className="cowork-mode"><RobotOutlined aria-hidden="true" /> 单智能体</span>
        {showUsage && <Popover trigger="click" placement="topRight" content={
          <div className="cowork-usage-detail">
            <strong>用量与上下文</strong>
            {chat.runMetrics.tokenKnown && <span>累计用量 {formatTokenCount(chat.runMetrics.totalTokens, true)} Token</span>}
            {contextLabel && <span>{contextLabel}</span>}
            <small>上下文为最近一次模型请求的输入量，历史由系统自动压缩。</small>
          </div>
        }><button type="button" className="cowork-usage" aria-label="查看用量与上下文"><InfoCircleOutlined aria-hidden="true" />上下文</button></Popover>}
      </div>
    </div>
  );

  const previewBody = activePreview && (
    <ChatExecutionPanel key={`${activePreview.runId}:${activePreview.kind}`} selectedRunId={activePreview.runId}
      reports={activePreview.kind === "data" ? [] : chat.reports.filter((item) => item.runId === activePreview.runId)}
      queryResults={activePreview.kind === "report" ? [] : chat.queryResults.filter((item) => item.runId === activePreview.runId)}
      chartByRunId={chat.chartByRunId} onPatchReport={chat.patchReport} />
  );
  const progressBody = <ChatTaskProgress key={latestRun} stages={stages} steps={latestSteps} running={loading} waiting={Boolean(pending) && !loading} />;
  const header = <header className="cowork-header">
    <div className="cowork-heading">
      <span className="cowork-header-label">智能分析</span>
      <h1 title={chat.conversationTitle || users[0]?.content}>{chat.conversationTitle || snippetConversationTitle(users[0]?.content || "")}</h1>
    </div>
    <div className="cowork-header-actions">
      <span className="cowork-run-state" data-state={loading ? "running" : waiting ? "waiting" : planComplete ? "completed" : "settled"}>
        {loading ? "分析中" : waiting ? "待回复" : planComplete ? "已完成" : "已结束"}
      </span>
      {!lg && <button type="button" className="cowork-icon" title="任务进度" aria-label="任务进度" aria-expanded={mobileProgressOpen && !activePreview}
        onClick={() => { setPreview(undefined); setMobileProgressOpen(true); }}><ApartmentOutlined /></button>}
      <button type="button" className="cowork-icon" disabled={!latestArtifacts && !activePreview} title="结果预览" aria-label="结果预览" aria-expanded={Boolean(activePreview)}
        onClick={() => setPreview(activePreview ? undefined : latestRun ? { runId: latestRun, kind: "all" } : undefined)}><EyeOutlined /></button>
      {loading && <button type="button" className="cowork-icon" title="停止任务" aria-label="停止任务" onClick={stop}><span className="cowork-stop-mark" aria-hidden="true" /></button>}
    </div>
  </header>;

  if (welcome) return (
    <section className="cowork-chat cowork-welcome" aria-label="新对话">
      <div className="cowork-welcome-inner">
        <img src={ASSISTANT_AVATAR} alt="" draggable={false} className="cowork-hero" />
        <h1>{ASSISTANT_WELCOME_TITLE}</h1>
        {composer}
      </div>
    </section>
  );

  return (
    <section className="cowork-chat" aria-label="聊天">
      <div className="cowork-main">
        {headerSlot ? createPortal(header, headerSlot) : header}
        <div className="cowork-messages" ref={viewport} onScroll={(event) => {
          const el = event.currentTarget;
          nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
          setShowBottom(!nearBottom.current);
        }}>
          <div className="cowork-transcript">
            {users.map((user, index) => {
              const runId = user.runId || "";
              const assistant = messages.find((item) => item.role === "assistant" && item.runId === runId);
              const running = loading && index === users.length - 1;
              const steps = chat.executionSteps.filter((step) => step.runId === runId);
              const reports = chat.reports.filter((item) => item.runId === runId);
              const results = chat.queryResults.filter((item) => item.runId === runId);
              const lastResult = results[results.length - 1];
              const chartRec = chat.chartByRunId[runId];
              const answer = assistant ? visibleMessage(assistant) : "";
              const thinkText = (chat.thinkByRunId[runId] || "").trim() || mergeThinkText(
                assistant ? extractThinkFromText(assistant.content) : "",
                extractThinkFromSteps(steps)
              );
              const process = <div className="cowork-process">
                <ChatWorkLog key={running ? "running" : "settled"} running={running} steps={steps} metrics={running ? chat.runMetrics : chat.metricsByRunId[runId]}
                  activity={activity} planned={Boolean(activeStage)} />
                {thinkText ? <ThinkBlock text={thinkText} live={running} /> : null}
              </div>;
              return (
                <article key={user.id} className="cowork-turn">
                  <div className="cowork-user">{user.content}</div>
                  <CopyRow item={user} />
                  {running && process}
                  {chat.clarifyByRunId[runId]?.status && chat.clarifyByRunId[runId].status !== "pending"
                    ? <HumanQuestionSummary question={chat.clarifyByRunId[runId]} />
                    : (assistant?.content || (!running && results.length > 0)) && !chat.clarifyByRunId[runId] && <>
                    <div className="cowork-answer">
                      <ReactMarkdown components={eduMarkdownComponents} remarkPlugins={markdownPlugins}>
                        {answer || (results.length ? "这轮未生成分析结论，请查看已返回的查询结果。" : "这轮未生成分析结论，请重新提问。")}
                      </ReactMarkdown>
                      {!running && lastResult ? (
                        <AnswerVisual
                          question={user.content}
                          columns={lastResult.columns}
                          rows={lastResult.rows}
                          rowCount={lastResult.rowCount}
                          spec={chartRec?.chartSpec ?? null}
                          legacyConfig={chartRec?.rawChartConfig}
                          legacyChartType={chartRec?.chartType}
                          onOpenPreview={() => setPreview({ runId, kind: "data" })}
                        />
                      ) : null}
                    </div>
                    {!running && answer && assistant && <CopyRow item={assistant} />}
                  </>}
                  {(reports.length > 0 || results.length > 0) && <div className="cowork-artifacts">
                    {reports.length > 0 && <button type="button" onClick={() => setPreview({ runId, kind: "report" })}><FileTextOutlined aria-hidden="true" /><span>分析报告 · {reports.length} 份</span></button>}
                    {results.length > 0 && <button type="button" onClick={() => setPreview({ runId, kind: "data" })}><DatabaseOutlined aria-hidden="true" /><span>查询结果 · {results.length} 组</span></button>}
                  </div>}
                  {!running && process}
                </article>
              );
            })}
          </div>
        </div>
        <div className="cowork-dock">
          <div className="cowork-dock-inner">
            {showBottom && <button className="cowork-to-bottom cowork-icon" type="button" aria-label="回到底部" title="回到底部" onClick={() => {
              nearBottom.current = true; setShowBottom(false);
              if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
            }}><ArrowDownOutlined /></button>}
            {loading && <ChatComposerStatus stage={activeStage?.content} activity={activity} metrics={chat.runMetrics} />}
            {pending && <div className="cowork-question" hidden={loading} role="group" aria-label="助手追问">
              <div className="cowork-question-header"><QuestionCircleOutlined aria-hidden="true" /><strong>需要你的回复</strong>
                <button type="button" className="cowork-icon" aria-expanded={!questionCollapsed}
                  onClick={() => setQuestionCollapsed(!questionCollapsed)}>{questionCollapsed ? "展开" : "收起"}</button>
              </div>
              <HumanQuestionForm key={pending.interrupt_id || pending.prompt} question={pending} hidden={questionCollapsed || loading}
                onSubmit={(answer) => send(answer, { datasourceId, resumeInterruptId: pending.interrupt_id })} />
            </div>}
            {(!pending || questionCollapsed || loading) && composer}
          </div>
        </div>
      </div>
      {activePreview && (lg ? <aside className="cowork-preview" aria-label="结果预览">
        <div className="cowork-preview-header"><strong>结果预览</strong><button type="button" className="cowork-icon" aria-label="关闭预览" onClick={() => setPreview(undefined)}><CloseCircleOutlined /></button></div>
        <div className="cowork-preview-body">{previewBody}</div>
      </aside> : <Drawer title="结果预览" width="min(100%, 560px)" open onClose={() => setPreview(undefined)}>{previewBody}</Drawer>)}
      {!activePreview && (lg ? progressOpen ? <aside className="cowork-task-panel" aria-label="任务进度">
        <header><ApartmentOutlined aria-hidden="true" /><strong>任务进度</strong><span className="cowork-panel-caption">单智能体</span>
          <button type="button" className="cowork-icon" aria-label="折叠任务进度" title="折叠任务进度" onClick={() => setProgressOpen(false)}><RightOutlined /></button>
        </header>
        {progressBody}
      </aside> : <div className="cowork-task-rail">
        <button type="button" className="cowork-icon" aria-label="展开任务进度" title="展开任务进度" onClick={() => setProgressOpen(true)}><LeftOutlined /></button>
        <span className="cowork-rail-label">任务进度</span>
      </div> : mobileProgressOpen && <Drawer title="任务进度" width="min(100%, 380px)" getContainer={false}
        rootStyle={{ position: "absolute" }} styles={{ body: { padding: 0 } }} open onClose={() => setMobileProgressOpen(false)}>{progressBody}</Drawer>)}
    </section>
  );
}

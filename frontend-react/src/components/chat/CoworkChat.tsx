import {
  ApartmentOutlined, ArrowDownOutlined, ArrowUpOutlined, BulbOutlined, CheckOutlined, CloseCircleOutlined, CopyOutlined,
  DatabaseOutlined, EyeOutlined, FileTextOutlined, LeftOutlined, MessageOutlined,
  PlusOutlined, QuestionCircleOutlined, RightOutlined, RobotOutlined,
} from "@ant-design/icons";
import { Drawer, Grid, Popover, message as toast } from "antd";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getCurrentUser } from "@/api/auth";
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

/** 思考流折叠区：流式期间默认展开并自动滚到底部，结束后可折叠回看。 */
function ThinkBlock({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(live);
  const body = useRef<HTMLPreElement>(null);
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
  const [input, setInput] = useState("");
  const [suggestions, setSuggestions] = useState<readonly string[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [preview, setPreview] = useState<{ runId: string; kind: "report" | "data" | "all" }>();
  const [progressOpen, setProgressOpen] = useState(true);
  const [mobileProgressOpen, setMobileProgressOpen] = useState(false);
  const [questionCollapsed, setQuestionCollapsed] = useState(false);
  const [showBottom, setShowBottom] = useState(false);
  const viewport = useRef<HTMLDivElement>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const nearBottom = useRef(true);
  const composing = useRef(false);
  const { lg } = Grid.useBreakpoint();

  useEffect(() => {
    let current = true;
    void getCurrentUser().then((user) => {
      if (current) setSuggestions(getAssistantSuggestions(user.edu_scope?.edu_role));
    }).catch(() => { if (current) setSuggestions(getAssistantSuggestions()); });
    return () => { current = false; };
  }, []);

  const users = messages.filter((item) => item.role === "user");
  const welcome = users.length === 0;
  const latestRun = users[users.length - 1]?.runId;
  const latestSteps = chat.executionSteps.filter((step) => step.runId === latestRun);
  const stages = latestRun ? chat.taskPlanByRunId[latestRun] ?? [] : [];
  const activeStage = stages.find((stage) => stage.status === "in_progress");
  const activity = runActivity(latestSteps, chat.activity);
  const pending = users.slice().reverse().map((user) => chat.clarifyByRunId[user.runId || ""])
    .find((question) => question && question.status !== "answered" && question.status !== "cancelled");
  const latestArtifacts = [...chat.reports, ...chat.queryResults].some((item) => item.runId === latestRun);
  const activePreview = preview && users.some((item) => item.runId === preview.runId) ? preview : undefined;

  useEffect(() => {
    setQuestionCollapsed(false);
    nearBottom.current = true;
    setShowBottom(false);
  }, [latestRun]);

  useEffect(() => {
    if (welcome) { setInput(""); setPreview(undefined); setMobileProgressOpen(false); }
  }, [welcome]);

  useEffect(() => {
    if (nearBottom.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [messages, chat.executionSteps, chat.reports, chat.queryResults, loading]);

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
          rows={welcome ? 3 : 2} maxLength={pending ? 8000 : 1000} disabled={loading} aria-label={pending ? "回复追问" : "输入消息"}
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
        {!welcome && chat.runMetrics.tokenKnown && <span className="cowork-token">{formatTokenCount(chat.runMetrics.totalTokens, true)} Token</span>}
        {!welcome && formatContextUsage(chat.runMetrics.contextUsage) && (
          <span className="cowork-token" title="最近一次主模型请求的输入 Token；累计用量另行统计。容量来自模型资料，历史由系统自动压缩。">
            {formatContextUsage(chat.runMetrics.contextUsage)}
          </span>
        )}
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
        <header className="cowork-header">
          <h1 title={chat.conversationTitle || users[0]?.content}>{chat.conversationTitle || snippetConversationTitle(users[0]?.content || "")}</h1>
          <div>
            {!lg && <button type="button" className="cowork-icon" title="任务进度" aria-label="任务进度" aria-expanded={mobileProgressOpen && !activePreview}
              onClick={() => { setPreview(undefined); setMobileProgressOpen(true); }}><ApartmentOutlined /></button>}
            <button type="button" className="cowork-icon" disabled={!latestArtifacts && !activePreview} title="结果预览" aria-label="结果预览" aria-expanded={Boolean(activePreview)}
              onClick={() => setPreview(activePreview ? undefined : latestRun ? { runId: latestRun, kind: "all" } : undefined)}><EyeOutlined /></button>
            <button type="button" className="cowork-icon" disabled={!loading} title="停止任务" aria-label="停止任务" onClick={stop}><span className="cowork-stop-mark" aria-hidden="true" /></button>
          </div>
        </header>
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
              const answer = assistant ? visibleMessage(assistant) : "";
              const thinkText = (chat.thinkByRunId[runId] || "").trim() || mergeThinkText(
                assistant ? extractThinkFromText(assistant.content) : "",
                extractThinkFromSteps(steps)
              );
              return (
                <article key={user.id} className="cowork-turn">
                  <div className="cowork-user">{user.content}</div>
                  <CopyRow item={user} />
                  <ChatWorkLog key={running ? "running" : "settled"} running={running} steps={steps} metrics={running ? chat.runMetrics : chat.metricsByRunId[runId]}
                    activity={activity} planned={Boolean(activeStage)} />
                  {thinkText ? <ThinkBlock text={thinkText} live={running} /> : null}
                  {chat.clarifyByRunId[runId]?.status && chat.clarifyByRunId[runId].status !== "pending"
                    ? <HumanQuestionSummary question={chat.clarifyByRunId[runId]} />
                    : assistant?.content && !chat.clarifyByRunId[runId] && <>
                    <div className="cowork-answer">
                      <ReactMarkdown components={eduMarkdownComponents} remarkPlugins={markdownPlugins}>
                        {answer || (results.length ? "这轮未生成分析结论，请查看已返回的查询结果。" : "这轮未生成分析结论，请重新提问。")}
                      </ReactMarkdown>
                    </div>
                    {!running && answer && <CopyRow item={assistant} />}
                  </>}
                  {(reports.length > 0 || results.length > 0) && <div className="cowork-artifacts">
                    {reports.length > 0 && <button type="button" onClick={() => setPreview({ runId, kind: "report" })}><FileTextOutlined aria-hidden="true" /><span>分析报告 · {reports.length} 份</span></button>}
                    {results.length > 0 && <button type="button" onClick={() => setPreview({ runId, kind: "data" })}><DatabaseOutlined aria-hidden="true" /><span>查询结果 · {results.length} 组</span></button>}
                  </div>}
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
        <header><ApartmentOutlined aria-hidden="true" /><strong>单智能体</strong>
          <button type="button" className="cowork-icon" aria-label="折叠任务进度" title="折叠任务进度" onClick={() => setProgressOpen(false)}><RightOutlined /></button>
        </header>
        {progressBody}
      </aside> : <div className="cowork-task-rail">
        <button type="button" className="cowork-icon" aria-label="展开任务进度" title="展开任务进度" onClick={() => setProgressOpen(true)}><LeftOutlined /></button>
      </div> : mobileProgressOpen && <Drawer title="任务进度" width="min(100%, 380px)" getContainer={false}
        rootStyle={{ position: "absolute" }} styles={{ body: { padding: 0 } }} open onClose={() => setMobileProgressOpen(false)}>{progressBody}</Drawer>)}
    </section>
  );
}

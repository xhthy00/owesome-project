import {
  ApartmentOutlined, ArrowDownOutlined, ArrowUpOutlined, BarChartOutlined, BookOutlined, BulbOutlined, CheckOutlined, CloseCircleOutlined, CopyOutlined,
  DatabaseOutlined, EyeOutlined, FileTextOutlined, FlagOutlined, InfoCircleOutlined,
  QuestionCircleOutlined, RightOutlined,
} from "@ant-design/icons";
import { Button, Drawer, Grid, Popover, message as toast } from "antd";
import { Bubble, Sender } from "@ant-design/x";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getCurrentUser } from "@/api/auth";
import ComposerLoader from "@/components/boardui/ComposerLoader";
import AnswerVisual from "@/components/chat/AnswerVisual";
import ChatExecutionPanel from "@/components/chat/ChatExecutionPanel";
import { ChatComposerStatus, ChatTaskProgress, ChatWorkLog, runActivity } from "@/components/chat/ChatRunProgress";
import HumanQuestionForm, { HumanQuestionSummary } from "@/components/chat/HumanQuestionForm";
import type { Message, useChat } from "@/hooks/useChat";
import { ASSISTANT_AVATAR, ASSISTANT_NAME } from "@/new-components/chat/assistant";
import { eduMarkdownComponents } from "@/new-components/chat/content/eduMarkdownComponents";
import { snippetConversationTitle } from "@/utils/conversationTitle";
import { formatContextUsage, formatTokenCount } from "@/utils/runMetrics";
import { extractThinkFromSteps, extractThinkFromText, mergeThinkText, splitThinkContent } from "@/utils/toolLabels";

const markdownPlugins = [remarkGfm];

const analysisScenarios = [
  { key: "comparison", label: "成绩对比", detail: "查看总分与各科变化", icon: BarChartOutlined,
    prompt: "请对比我有权查看的范围内近三次可比考试的成绩，展示总分均分和各科变化。" },
  { key: "reach", label: "达线分析", detail: "查看达线人数与比例", icon: FlagOutlined,
    prompt: "请分析最近一次考试的本科线和特控线达线情况，列出达线人数及达线率。" },
  { key: "subjects", label: "薄弱学科", detail: "找出需要优先关注的学科", icon: BookOutlined,
    prompt: "请分析最近一次考试中需要重点关注的学科，并根据成绩数据给出改进建议。" },
  { key: "report", label: "生成报告", detail: "汇总考试表现和建议", icon: FileTextOutlined,
    prompt: "请为我有权查看的范围生成最近一次考试的学情报告，包含成绩概况、重点发现和改进建议。" },
];

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
      <Button type="text" size="small" title="复制消息" aria-label={copied ? "已复制" : "复制消息"}
        icon={copied ? <CheckOutlined /> : <CopyOutlined />}
        onClick={async () => {
          try { await navigator.clipboard.writeText(visibleMessage(item)); setCopied(true); }
          catch { void toast.error("复制失败"); }
        }} />
    </div>
  );
}

export default function CoworkChat({ chat }: { chat: ChatViewState }) {
  const { messages, loading, send, stop, datasourceId } = chat;
  const users = messages.filter((item) => item.role === "user");
  const welcome = users.length === 0;
  const [input, setInput] = useState("");
  const [personalScope, setPersonalScope] = useState(false);
  const [preview, setPreview] = useState<{ runId: string; kind: "report" | "data" | "all" }>();
  const [progressOpen, setProgressOpen] = useState(true);
  const [questionCollapsed, setQuestionCollapsed] = useState(false);
  const [showBottom, setShowBottom] = useState(false);
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const editor = useRef<React.ComponentRef<typeof Sender>>(null);
  const nearBottom = useRef(true);
  const lastScrollRun = useRef<string | undefined>(undefined);
  const wasLoading = useRef(false);
  const { lg } = Grid.useBreakpoint();

  useEffect(() => { setHeaderSlot(document.getElementById("chat-header-slot")); }, []);

  // 技能市场的“在 AI 问数中打开”：把提示词预填进输入框，但不自动提交。
  // 带 q 参数的深链会自己发起提问，这里直接丢弃预填，避免抢输入。
  useEffect(() => {
    let prefill = "";
    try {
      prefill = sessionStorage.getItem("prefill_prompt") ?? "";
      if (prefill) sessionStorage.removeItem("prefill_prompt");
    } catch {
      return;
    }
    if (!prefill || new URLSearchParams(window.location.search).get("q")) return;
    setInput(prefill);
    window.requestAnimationFrame(() => editor.current?.focus());
  }, []);

  useEffect(() => {
    let current = true;
    void getCurrentUser().then((user) => {
      if (!current) return;
      const scope = user.edu_scope;
      setPersonalScope(scope?.edu_role === "student" || Boolean(scope?.levels?.length === 1 && scope.levels[0] === "student"));
    }).catch(() => {});
    return () => { current = false; };
  }, []);

  const firstTurn = users[0]?.id;
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
  }, [latestRun]);

  useEffect(() => {
    setProgressOpen(true);
    setPreview(undefined);
  }, [firstTurn]);

  useEffect(() => {
    if (welcome) setInput("");
  }, [welcome]);

  useEffect(() => {
    const resetInput = () => setInput("");
    window.addEventListener("chat:new-task", resetInput);
    return () => window.removeEventListener("chat:new-task", resetInput);
  }, []);

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
    if (!value.trim() || loading || value.length > (pending ? 8000 : 1000)) return;
    setInput("");
    void send(value.trim(), { datasourceId, ...(pending?.interrupt_id ? { resumeInterruptId: pending.interrupt_id } : {}) });
  };

  const keyboardHint = (
    <div id="cowork-composer-hint" className="cowork-composer-hint">
      <span>Enter 发送</span><span aria-hidden="true">·</span><span>Shift + Enter 换行</span>
    </div>
  );
  const composer = (
    <div className="cowork-composer-block">
      <div className="cowork-composer">
        <span className="cowork-composer-surface" aria-hidden="true" />
        <ComposerLoader active={loading} radius={16} />
        <div className="cowork-composer-content">
          {welcome && <header className="cowork-welcome-head">
            <div className="cowork-welcome-copy">
              <h1 className="cowork-welcome-title">{ASSISTANT_NAME}</h1>
              <p className="cowork-welcome-description">想分析哪场考试？告诉我你关注的年级、班级或学科。</p>
            </div>
            <img className="cowork-hero" src="/assistant-welcome.png" width={112} height={108} alt="" />
          </header>}
          <Sender ref={editor} className="cowork-sender" value={input} loading={loading} readOnly={loading}
            classNames={{ content: "cowork-sender-content", footer: "cowork-sender-footer" }}
            autoSize={{ minRows: welcome ? 2 : 1, maxRows: 7 }}
            submitType="enter"
            aria-label={pending ? "回复追问" : "输入消息"}
            aria-describedby="cowork-composer-hint"
            onChange={(value) => setInput(value.slice(0, pending ? 8000 : 1000))}
            onKeyDown={(event) => event.nativeEvent.isComposing ? false : undefined}
            onSubmit={submit} onCancel={stop}
            placeholder={pending ? "回复助手的问题…" : loading ? "正在分析…" : welcome ? "例如：对比高三年级近三次考试，找出成绩下滑的学科" : "继续追问…"}
            suffix={false}
            footer={(_, { components: { SendButton, LoadingButton } }) => <div className="cowork-composer-footer">
              {welcome ? keyboardHint : <span className="cowork-composer-caption"><BulbOutlined aria-hidden="true" />
                {pending ? "补充信息后继续分析" : loading ? "正在分析，可随时停止" : "描述你的分析需求"}
              </span>}
              <div className="cowork-composer-toolbar">
                {showUsage && <Popover trigger="click" placement="topRight" content={
                  <div className="cowork-usage-detail">
                    <strong>用量与上下文</strong>
                    {chat.runMetrics.tokenKnown && <span>累计用量 {formatTokenCount(chat.runMetrics.totalTokens, true)} Token</span>}
                    {contextLabel && <span>{contextLabel}</span>}
                    <small>上下文为最近一次模型请求的输入量，历史由系统自动压缩。</small>
                  </div>
                }>
                  <Button type="text" shape="circle" icon={<InfoCircleOutlined />} aria-label="查看用量与上下文" title="上下文" />
                </Popover>}
              {loading
                ? <LoadingButton shape="default" aria-label="停止任务" title="停止任务" />
                : <SendButton shape="default" aria-label="发送消息" title="发送消息" disabled={!input.trim()} icon={<ArrowUpOutlined />} />}
              </div>
            </div>}
          />
        </div>
      </div>
      {!welcome && keyboardHint}
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
      <Button type={progressOpen && !activePreview ? "default" : "text"} size="small" className="cowork-progress-trigger"
        title="任务进度" aria-label="任务进度" aria-expanded={progressOpen && !activePreview} icon={<ApartmentOutlined />}
        onClick={() => { setPreview(undefined); setProgressOpen(Boolean(activePreview) || !progressOpen); }}>
        {lg && "任务进度"}
      </Button>
      <button type="button" className="cowork-icon" disabled={!latestArtifacts && !activePreview} title="结果预览" aria-label="结果预览" aria-expanded={Boolean(activePreview)}
        onClick={() => setPreview(activePreview ? undefined : latestRun ? { runId: latestRun, kind: "all" } : undefined)}><EyeOutlined /></button>
      {loading && <button type="button" className="cowork-icon" title="停止任务" aria-label="停止任务" onClick={stop}><span className="cowork-stop-mark" aria-hidden="true" /></button>}
    </div>
  </header>;

  if (welcome) return (
    <section className="cowork-chat cowork-welcome" aria-label="新对话">
      <div className="cowork-welcome-breadcrumb"><span>AI 学情分析</span><span>新任务</span></div>
      <div className="cowork-welcome-inner">
        {composer}
        <section className="cowork-scenarios" aria-labelledby="cowork-scenarios-title">
          <h2 id="cowork-scenarios-title">快捷分析</h2>
          <div className="cowork-starter-list">
            {analysisScenarios.map(({ key, label, detail, icon: Icon, prompt }) => <button
              key={key} type="button" className="cowork-starter"
              onClick={() => {
                setInput(personalScope && key === "reach"
                  ? "请查看我最近一次考试的成绩，分析与对应本科线、特控线的差距。" : prompt);
                window.requestAnimationFrame(() => editor.current?.focus({ cursor: "end" }));
              }}>
              <span className="cowork-starter-icon"><Icon aria-hidden="true" /></span>
              <span className="cowork-starter-copy"><strong>{label}</strong><span>{personalScope && key === "reach" ? "查看我的成绩距分数线有多远" : detail}</span></span>
              <ArrowUpOutlined className="cowork-starter-arrow" aria-hidden="true" />
            </button>)}
          </div>
        </section>
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
                  <Bubble content={user.content} placement="end" shape="corner"
                    classNames={{ content: "cowork-user" }} footer={<CopyRow item={user} />} />
                  {running && process}
                  {chat.clarifyByRunId[runId]?.status && chat.clarifyByRunId[runId].status !== "pending"
                    ? <HumanQuestionSummary question={chat.clarifyByRunId[runId]} />
                    : (assistant?.content || (!running && results.length > 0)) && !chat.clarifyByRunId[runId] && <>
                    <Bubble<React.ReactNode> variant="borderless" className="cowork-response"
                      classNames={{ body: "cowork-response-body", content: "cowork-answer" }}
                      header={<span className="cowork-assistant-label"><img src={ASSISTANT_AVATAR} alt="" />智学助手</span>}
                      footer={!running && answer && assistant ? <CopyRow item={assistant} /> : undefined}
                      content={<>
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
                      </>} />
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
            {(!pending || loading) && composer}
          </div>
        </div>
      </div>
      {activePreview && (lg ? <aside className="cowork-preview" aria-label="结果预览">
        <div className="cowork-preview-header"><strong>结果预览</strong><button type="button" className="cowork-icon" aria-label="关闭预览" onClick={() => setPreview(undefined)}><CloseCircleOutlined /></button></div>
        <div className="cowork-preview-body">{previewBody}</div>
      </aside> : <Drawer title="结果预览" width="min(100%, 560px)" open onClose={() => setPreview(undefined)}>{previewBody}</Drawer>)}
      {progressOpen && !activePreview && (lg ? <aside className="cowork-task-panel" aria-label="任务进度">
        <header><ApartmentOutlined aria-hidden="true" /><strong>任务进度</strong>
          <button type="button" className="cowork-icon" aria-label="折叠任务进度" title="折叠任务进度" onClick={() => setProgressOpen(false)}><RightOutlined /></button>
        </header>
        {progressBody}
      </aside> : <Drawer title="任务进度" width="min(100%, 380px)" getContainer={false}
        rootStyle={{ position: "absolute" }} styles={{ body: { padding: 0 } }} open onClose={() => setProgressOpen(false)}>{progressBody}</Drawer>)}
    </section>
  );
}

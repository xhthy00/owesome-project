import { BulbOutlined, CheckOutlined, FileTextOutlined, OrderedListOutlined, ToolOutlined } from "@ant-design/icons";
import type { TaskPlanStage } from "@/api/adapter/chatAdapter";
import type { ExecutionStep } from "@/hooks/useChat";
import type { RunMetrics } from "@/utils/runMetrics";
import { splitThinkContent } from "@/utils/toolLabels";

const toolNames: Record<string, string> = {
  get_analysis_context: "读取语义模型",
  describe_semantic_model: "查看语义模型字段",
  query_semantic: "查询数据",
  lookup_question_context: "确认分析范围",
  execute_sql: "查询数据",
  chart_semantic_result: "生成图表",
  ask_human: "向用户确认信息",
  write_todos: "更新任务计划",
};

function toolTitle(tool: string) {
  return toolNames[tool] || tool;
}

function elapsedLabel(metrics?: RunMetrics) {
  if (!metrics?.elapsedKnown) return "";
  const seconds = Math.floor(metrics.elapsedMs / 1000);
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}分${seconds % 60}秒` : `${seconds}秒`;
}

function Orb() {
  return <span className="cowork-orb" aria-hidden="true" />;
}

export function runActivity(steps: ExecutionStep[], activity: string) {
  for (let index = steps.length - 1; index >= 0; index--) {
    const step = steps[index];
    if (step.toolName && step.status === "running") {
      return `正在执行 · ${toolTitle(step.toolName)}`;
    }
  }
  return activity || "正在准备执行";
}

function TraceRow({ step, running }: { step: ExecutionStep; running: boolean }) {
  const title = step.toolName ? toolTitle(step.toolName) : step.title;
  const note = step.kind === "thought" && splitThinkContent(step.detail || "").plain;
  const status = step.status === "error" ? "未完成" : step.status === "running" ? (running ? "执行中" : "已中断") : "";
  const preview = [
    step.rowCount != null ? `返回 ${step.rowCount} 行` : "",
    step.elapsedMs != null ? `${step.elapsedMs} 毫秒` : "",
  ].filter(Boolean).join(" · ");
  return <li data-status={step.status}>
    <div className={`cowork-trace-line${step.toolName ? " cowork-trace-tool" : ""}`}>
      {step.kind === "thought" ? <BulbOutlined aria-hidden="true" /> : step.toolName === "write_todos"
        ? <OrderedListOutlined aria-hidden="true" /> : step.toolName
          ? <ToolOutlined aria-hidden="true" /> : <span className="cowork-trace-dot" aria-hidden="true" />}
      <span className="cowork-trace-title" title={title}>{title}</span>
      {(status || preview) && <span className="cowork-trace-status">{status || preview}</span>}
    </div>
    {note && <p>{note}</p>}
  </li>;
}

function TraceRows({ steps, running }: { steps: ExecutionStep[]; running: boolean }) {
  const groups: ExecutionStep[][] = [];
  for (const step of steps) {
    const previous = groups[groups.length - 1];
    if (step.toolName && previous?.[0].toolName === step.toolName) previous.push(step);
    else groups.push([step]);
  }
  return <ol className="cowork-trace-list">
    {groups.map((group) => {
      const first = group[0];
      if (group.length === 1 || !first.toolName) return <TraceRow key={first.id} step={first} running={running} />;
      const active = running && group.some((step) => step.status === "running");
      const failed = group.filter((step) => step.status === "error").length;
      return <li key={first.id}>
        <details className="cowork-trace-group">
          <summary className="cowork-trace-line">
            <span className="cowork-trace-title">{toolTitle(first.toolName)} · {group.length} 次调用</span>
            {active ? <span className="cowork-trace-status">执行中</span>
              : failed > 0 && <span className="cowork-trace-status">{failed} 次未完成</span>}
          </summary>
          <ol className="cowork-trace-list">{group.map((step) => <TraceRow key={step.id} step={step} running={running} />)}</ol>
        </details>
      </li>;
    })}
  </ol>;
}

export function ChatWorkLog({ steps, running, metrics, activity, planned }: {
  steps: ExecutionStep[]; running: boolean; metrics?: RunMetrics; activity: string; planned: boolean;
}) {
  if (!steps.length && !running) return null;
  const elapsed = elapsedLabel(metrics);
  const tools = steps.filter((step) => step.toolName).length;
  return <details className="cowork-worklog">
    <summary>
      {running && <Orb />}
      <span className={running ? "cowork-shimmer" : undefined}>
        {running || !elapsed ? `执行记录 · ${tools} 次工具调用${elapsed ? ` · ${elapsed}` : ""}` : `已工作 ${elapsed} · ${tools} 次工具调用`}
      </span>
    </summary>
    {running && <div className="cowork-worklog-live">
      <span key={activity}>{activity}</span>
      <small>{planned ? "按计划执行中" : activity.startsWith("正在执行") ? "处理当前步骤" : "正在梳理分析思路"}</small>
    </div>}
    <TraceRows steps={steps} running={running} />
  </details>;
}

export function ChatComposerStatus({ stage, activity, metrics }: { stage?: string; activity: string; metrics: RunMetrics }) {
  return <div className="cowork-live" role="status">
    <Orb />
    <div key={stage || activity} className="cowork-live-label">
      <div className="cowork-shimmer">{stage || activity}</div>
      <small>{stage ? `${activity} · 按计划执行中` : activity.startsWith("正在执行") ? "处理当前步骤" : "正在梳理分析思路"}</small>
    </div>
    <time aria-hidden="true">已工作 {elapsedLabel(metrics)}</time>
  </div>;
}

export function ChatTaskProgress({ stages, steps, running, waiting }: {
  stages: TaskPlanStage[]; steps: ExecutionStep[]; running: boolean; waiting: boolean;
}) {
  const completed = stages.filter((stage) => stage.status === "completed").length;
  const percent = stages.length ? Math.round(completed / stages.length * 100) : 0;
  const contextTools = Array.from(new Set(steps.flatMap((step) => step.toolName ? [step.toolName] : [])));
  return <div className="cowork-task-body">
    <details className="cowork-task-section" open>
      <summary>进度 {stages.length > 0 && <span>{completed} / {stages.length}</span>}</summary>
      <div className="cowork-task-section-body">
        {stages.length ? <>
          <div className="cowork-progress-overview">
            <div><span>阶段完成情况</span><strong>{completed} / {stages.length} 个阶段 · {percent}%</strong></div>
            <div className="cowork-progress-track" role="progressbar" aria-label="任务完成进度"
              aria-valuenow={percent} aria-valuetext={`${completed} / ${stages.length} 个阶段，${percent}%`}>
              <span style={{ transform: `scaleX(${completed / stages.length})` }} />
            </div>
          </div>
          <ol className="cowork-stage-list">
            {stages.map((stage, index) => {
              const status = !running && !waiting && ["pending", "in_progress"].includes(stage.status) ? "unconfirmed" : stage.status;
              const label = { pending: "待执行", in_progress: waiting ? "待继续" : "进行中", completed: "已完成", failed: "未完成", unconfirmed: "未确认完成" }[status];
              const related = steps.filter((step) => step.toolName && (
                step.stageContent ? step.stageContent === stage.content : step.subTaskIndex === index
              ));
              const row = <>
                <span className="cowork-stage-index" aria-hidden="true">{status === "completed" ? <CheckOutlined /> : String(index + 1).padStart(2, "0")}</span>
                <span className="cowork-stage-title">{stage.content}</span>
                <span className="cowork-stage-status">{label}</span>
              </>;
              return <li key={`${index}:${stage.content}`} className="cowork-stage" data-status={status}>
                {related.length ? <details><summary className="cowork-stage-row">{row}</summary><TraceRows steps={related} running={running} /></details>
                  : <div className="cowork-stage-row">{row}</div>}
              </li>;
            })}
          </ol>
        </> : <p className="cowork-progress-empty">{running ? "尚未收到步骤规划" : "本轮没有发布阶段计划"}</p>}
      </div>
    </details>
    {contextTools.length > 0 && <details className="cowork-task-section" open>
      <summary>执行上下文</summary>
      <div className="cowork-task-section-body">
        <span className="cowork-task-context-label">工具</span>
        <ul className="cowork-task-context-list">{contextTools.map((tool) => <li key={tool}><FileTextOutlined aria-hidden="true" />{toolTitle(tool)}</li>)}</ul>
      </div>
    </details>}
    {steps.length > 0 && <details className="cowork-task-section">
      <summary>Trace <span>{steps.length}</span></summary>
      <TraceRows steps={steps} running={running} />
    </details>}
  </div>;
}

import { useEffect, useRef, useState } from 'react';
import type { ComponentRef } from 'react';
import { createPortal } from 'react-dom';
import { ApartmentOutlined, BarChartOutlined, CheckCircleOutlined, CloseOutlined, CodeOutlined, DatabaseOutlined, EyeOutlined, HistoryOutlined, LeftOutlined, PlusOutlined, QuestionCircleOutlined, RightOutlined, StopOutlined, TableOutlined } from '@ant-design/icons';
import { Bubble, Conversations, Sender } from '@ant-design/x';
import { Avatar, BorderBeam, Button, Drawer, Grid, Select, Space, Steps, Table, Tabs, Tag, Tooltip } from 'antd';
import type { BorderBeamGradient } from 'antd';
import { exams, prompts, schools, subjectRates } from './data';
import { SubjectChart } from './dashboard';
import assistantAvatar from './assistant-avatar.png';

type Turn = { id: string; question: string; answer: string; status: 'streaming' | 'complete' | 'stopped'; source?: string; needsScope?: boolean; step?: number };
type Thread = { id: string; title: string; turns: Turn[] };
const sample: Thread = { id: 'example', title: '八年级各科达线率对比', turns: [{ id: 'sample', question: prompts[0], answer: '本次考试道法、英语达线率领先，科学和语文仍有提升空间。和上次考试相比，英语的提升幅度最大，增加 6.3 个百分点。', status: 'complete' }] };
const sourceNames = ['区级学情分析库', '滨江实验中学成绩库'];
const taskSteps = ['理解问题', '查询数据', '生成结果'];
const aurora: BorderBeamGradient = [
  { color: '#7c3aed', percent: 0 },
  { color: '#06b6d4', percent: 57 },
  { color: '#67e8f9', percent: 100 },
];

export function Chat({ visible, headerSlot, taskSlot, onNavigateTask }: {
  visible: boolean;
  headerSlot: HTMLElement | null;
  taskSlot: HTMLElement | null;
  onNavigateTask: () => void;
}) {
  const [threads, setThreads] = useState([sample]);
  const [active, setActive] = useState('');
  const [input, setInput] = useState('');
  const [source, setSource] = useState(sourceNames[0]);
  const [run, setRun] = useState<{ thread: string; turn: string }>();
  const [inspecting, setInspecting] = useState(false);
  const [panel, setPanel] = useState('chart');
  const [resultId, setResultId] = useState<string>();
  const [progressOpen, setProgressOpen] = useState(true);
  const [mobileProgress, setMobileProgress] = useState(false);
  const desktop = Boolean(Grid.useBreakpoint().lg);
  const interval = useRef<number | undefined>(undefined);
  const transcript = useRef<HTMLDivElement>(null);
  const sender = useRef<ComponentRef<typeof Sender>>(null);
  const current = threads.find(thread => thread.id === active);
  const turns = current?.turns ?? [];
  const selectedTurn = turns.find(turn => turn.id === resultId);
  const latest = turns.at(-1);
  const latestResult = turns.slice().reverse().find(turn => turn.status === 'complete' && !turn.needsScope);
  const pending = latest?.status === 'complete' && latest.needsScope;
  const running = run?.thread === active;

  useEffect(() => () => window.clearInterval(interval.current), []);
  useEffect(() => {
    if (visible && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [visible, turns]);

  function switchThread(id: string) {
    setActive(id);
    setInput('');
    setInspecting(false);
    setResultId(undefined);
    setMobileProgress(false);
    onNavigateTask();
  }

  function updateTurn(threadId: string, turnId: string, update: Partial<Turn>) {
    setThreads(previous => previous.map(thread => thread.id === threadId ? { ...thread, turns: thread.turns.map(turn => turn.id === turnId ? { ...turn, ...update } : turn) } : thread));
  }

  function send(value: string, clarified = false) {
    const question = value.trim();
    if (!question || run) return;
    const thread = active || crypto.randomUUID();
    const id = crypto.randomUUID();
    const needsScope = !clarified && /哪些班级|需要关注/.test(question);
    const answer = needsScope ? '请先选一个统计范围。你想查看全区，还是只看滨江实验中学？' : /英语/.test(question) && !/各科/.test(question) ? '英语达线率为 86.8%，较上次提高 6.3 个百分点。建议进一步查看各校阅读理解题组的得分差异。' : clarified ? `已切换到${question}范围。样例结果显示数学较上次达线率有所提升，建议优先查看计算题失分情况。` : sample.turns[0].answer;
    const turn: Turn = { id, question, answer: '', status: 'streaming', source, needsScope, step: 0 };
    setThreads(previous => active ? previous.map(item => item.id === active ? { ...item, turns: [...item.turns, turn] } : item) : [{ id: thread, title: question, turns: [turn] }, ...previous]);
    setActive(thread);
    setInput('');
    setInspecting(false);
    setRun({ thread, turn: id });
    let cursor = 0;
    let tick = 0;
    interval.current = window.setInterval(() => {
      tick += 1;
      if (tick <= 6) return;
      if (!needsScope && tick <= 15) {
        if (tick === 7) updateTurn(thread, id, { step: 1 });
        return;
      }
      cursor += 3;
      const finished = cursor >= answer.length;
      updateTurn(thread, id, { answer: answer.slice(0, cursor), step: needsScope ? 0 : 2, status: finished ? 'complete' : 'streaming' });
      if (finished) {
        window.clearInterval(interval.current);
        setRun(undefined);
      }
    }, 45);
  }

  function stop() {
    if (!run) return;
    window.clearInterval(interval.current);
    updateTurn(run.thread, run.turn, { status: 'stopped' });
    setRun(undefined);
  }

  function inspect(id: string, tab: string) {
    setResultId(id);
    setPanel(tab);
    setInspecting(true);
  }

  const scope = selectedTurn?.question === schools[1] || selectedTurn?.source === sourceNames[1] ? schools[1] : schools[0];
  const state = running ? '分析中' : pending ? '待回复' : latest?.status === 'stopped' ? '已停止' : '已完成';
  const composer = <BorderBeam className="composer-aurora" color={aurora} lineWidth={3} duration={6} outset={2}>
    <div className={running ? 'composer composer-running' : 'composer'}>
    <Sender ref={sender} value={input} onChange={setInput} onSubmit={value => send(value)} onCancel={stop} loading={Boolean(run)} placeholder="问问成绩、达线率或知识点…"
      footer={(_, { components: { LoadingButton, SendButton } }) => <div className="composer-tools">
        <Select aria-label="数据源" size="small" variant="borderless" value={source} onChange={setSource} options={sourceNames.map(value => ({ value, label: <><DatabaseOutlined /> {value}</> }))} />
        {run ? <LoadingButton aria-label="停止生成" /> : <SendButton aria-label="发送问题" />}
      </div>} suffix={false} />
    </div>
  </BorderBeam>;

  const preview = selectedTurn && <div className="result-content">
    <div className="result-context"><DatabaseOutlined />{selectedTurn.source ?? sourceNames[0]} · {scope}</div>
    <Tabs activeKey={panel} onChange={setPanel} items={[
      { key: 'chart', label: '图表', icon: <BarChartOutlined />, children: <SubjectChart exam={exams[0]} school={scope} compact /> },
      { key: 'table', label: '数据', icon: <TableOutlined />, children: <Table size="small" rowKey="name" dataSource={subjectRates(exams[0], scope)} pagination={false} columns={[
        { title: '学科', dataIndex: 'name' }, { title: '本次', dataIndex: 'current', render: value => value + '%' }, { title: '上次', dataIndex: 'previous', render: value => value + '%' },
      ]} /> },
      { key: 'sql', label: 'SQL', icon: <CodeOutlined />, children: <pre className="sql">{`SELECT subject,\n       ROUND(AVG(reached) * 100, 1) AS rate\nFROM exam_results\nWHERE exam_id = :exam_id\nGROUP BY subject\nORDER BY rate DESC;`}</pre> },
    ]} destroyOnHidden />
  </div>;

  const step = latest?.step ?? 0;
  const progress = <Steps className={running ? 'task-steps task-steps-running' : 'task-steps'} orientation="vertical" size="small"
    current={state === '已完成' ? taskSteps.length : step} status={state === '已停止' ? 'error' : 'process'}
    items={taskSteps.map((title, index) => ({ title, description: index === 0 && pending ? '等待选择统计范围' : undefined }))} />;

  return <>
    {taskSlot && createPortal(<>
      <Button className="new-task" block icon={<PlusOutlined />} onClick={() => { stop(); switchThread(''); }}>新建任务</Button>
      <div className="sidebar-tasks">
        <div className="nav-label"><HistoryOutlined /> 最近任务</div>
        <Conversations activeKey={active} onActiveChange={switchThread} items={threads.map(thread => ({ key: thread.id, label: thread.title, title: thread.title }))} />
      </div>
    </>, taskSlot)}
    {visible && headerSlot && createPortal(<div className="chat-toolbar">
      <div className="chat-heading">
        {current && <span>智能分析</span>}
        <h1 title={current?.title}>{current?.title ?? 'AI 问数'}</h1>
      </div>
      {current && <div className="chat-toolbar-actions">
        <Tag color={running ? 'processing' : pending ? 'warning' : state === '已停止' ? 'default' : 'success'}>{state}</Tag>
        {!desktop && <Tooltip title="任务进度"><Button size="small" type="text" aria-label="任务进度" icon={<ApartmentOutlined />} onClick={() => { setInspecting(false); setMobileProgress(true); }} /></Tooltip>}
        <Tooltip title="结果预览"><Button size="small" type="text" aria-label="结果预览" disabled={!latestResult} icon={<EyeOutlined />} onClick={() => { if (inspecting) setInspecting(false); else if (latestResult) inspect(latestResult.id, 'chart'); }} /></Tooltip>
      </div>}
    </div>, headerSlot)}
    <section className={`chat-shell ${current && desktop ? (inspecting ? 'with-result' : progressOpen ? 'with-progress' : 'with-rail') : ''}`} aria-label="AI 问数工作区">
      {!current ? <div className="chat-welcome">
        <div className="chat-welcome-inner">
          <div className="chat-welcome-heading"><img src={assistantAvatar} alt="" /><h2>你好，我是智学助手</h2></div>
          {composer}
          <div className="prompt-list" role="group" aria-label="推荐问题">{prompts.map(prompt => (
            <Button className="starter" key={prompt} icon={<RightOutlined />} onClick={() => { setInput(prompt); sender.current?.focus(); }}>{prompt}</Button>
          ))}</div>
        </div>
      </div> : <div className="chat-main">
        <div className="transcript" ref={transcript}>
          <div className="transcript-inner">{turns.map(turn => <article className="chat-turn" key={turn.id}>
            <Bubble placement="end" className="chat-user" content={turn.question} />
            <Bubble placement="start" className="chat-answer" variant="borderless" avatar={
              <span className={turn.status === 'streaming' ? 'chat-avatar chat-avatar-running' : 'chat-avatar'}><Avatar src={assistantAvatar} size={30} /></span>
            }
              header={<span className="assistant-name">智学助手</span>}
              loading={!turn.answer && turn.status === 'streaming'} content={turn.answer || (turn.status === 'stopped' ? '已停止生成。' : '')} />
            {turn.status === 'stopped' && turn.answer && <div className="stopped-label"><StopOutlined /> 已停止生成</div>}
            {turn.status === 'complete' && !turn.needsScope && <div className={turn === latest ? 'chat-artifact chat-artifact-latest' : 'chat-artifact'}>
              <div className="artifact-icon"><BarChartOutlined /></div><div className="artifact-label"><strong>学科达线率</strong><small>{exams[0]}</small></div>
              <Space wrap size={4}><Button size="small" type="text" onClick={() => inspect(turn.id, 'chart')}>查看图表</Button><Button size="small" type="text" onClick={() => inspect(turn.id, 'table')}>数据</Button><Button size="small" type="text" onClick={() => inspect(turn.id, 'sql')}>SQL</Button></Space>
            </div>}
          </article>)}</div>
        </div>
        <div className="chat-dock">
          {running && <div className="chat-live" role="status"><span aria-hidden="true" />{taskSteps[step]}</div>}
          {pending ? <section className="clarification" aria-label="助手追问"><strong><QuestionCircleOutlined /> 选择统计范围</strong><Space wrap>
            {schools.slice(0, 2).map(school => <Button size="small" key={school} onClick={() => send(school, true)}>{school}</Button>)}
          </Space></section> : composer}
        </div>
      </div>}
      {current && desktop && (inspecting ? <aside className="result-panel" aria-label="结果预览">
        <div className="result-title"><h2><BarChartOutlined /> 结果预览</h2><Button size="small" aria-label="关闭预览" type="text" icon={<CloseOutlined />} onClick={() => setInspecting(false)} /></div>{preview}
      </aside> : progressOpen ? <aside className="result-panel task-panel" aria-label="任务进度">
        <div className="result-title"><h2><ApartmentOutlined /> 任务进度</h2><Button size="small" aria-label="折叠任务进度" type="text" icon={<RightOutlined />} onClick={() => setProgressOpen(false)} /></div>
        {progress}
        {state === '已完成' && <div className="progress-done"><CheckCircleOutlined /> 已完成</div>}
      </aside> : <aside className="task-rail">
        <Button size="small" type="text" aria-label="展开任务进度" icon={<LeftOutlined />} onClick={() => setProgressOpen(true)} />
        <span aria-hidden="true">任务进度</span>
      </aside>)}
      {visible && !desktop && <Drawer title={inspecting ? '结果预览' : '任务进度'} open={inspecting || mobileProgress} size={420} onClose={() => { setInspecting(false); setMobileProgress(false); }}>{inspecting ? preview : progress}</Drawer>}
    </section>
  </>;
}

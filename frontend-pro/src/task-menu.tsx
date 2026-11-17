import { DeleteOutlined, PlusOutlined, RightOutlined } from '@ant-design/icons';
import { Conversations } from '@ant-design/x';
import { App, Button, Collapse, Tooltip } from 'antd';
import { history, Link, useLocation } from '@umijs/max';
import { useEffect, useState } from 'react';
import { deleteConversation, listConversations } from '@/api/adapter/chatAdapter';
import type { Conversation } from '@/api/adapter/chatAdapter';
import { notifyConversationChanged } from '@/utils/conversationTitle';
import { useSession } from '@/workspace';

function dateKey(date: Date) {
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
}

function recentGroups(tasks: Conversation[], now = new Date()) {
  const days = Array.from({ length: 5 }, (_, offset) => {
    const key = dateKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - offset));
    return { key, label: offset === 0 ? '今天' : offset === 1 ? '昨天' : key, openByDefault: false, tasks: [] as { task: Conversation; time: number }[] };
  });
  const byKey = new Map(days.map(day => [day.key, day]));
  for (const task of tasks) {
    const date = [task.update_time, task.create_time].filter(Boolean).map(value => new Date(value!))
      .find(value => Number.isFinite(value.getTime()));
    if (date) byKey.get(dateKey(date))?.tasks.push({ task, time: date.getTime() });
  }
  return days.filter(day => day.tasks.length).map(day => ({
    ...day, tasks: day.tasks.sort((a, b) => b.time - a.time).map(item => item.task),
  }));
}

function timeLabel(task: Conversation) {
  const raw = task.update_time || task.create_time;
  if (!raw) return '';
  const date = new Date(raw);
  if (!Number.isFinite(date.getTime())) return '';
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function TaskMenu() {
  const { session } = useSession();
  const { search } = useLocation();
  const { modal, message } = App.useApp();
  const [tasks, setTasks] = useState<Conversation[]>([]);
  const [openByDate, setOpenByDate] = useState<Record<string, boolean>>({});
  const activeId = new URLSearchParams(search).get('conversation_id') ?? undefined;

  useEffect(() => {
    let active = true;
    setTasks([]);
    if (!session) return;
    async function load() {
      try {
        const rows = await listConversations();
        if (active) setTasks(rows);
      } catch {
        if (active) setTasks([]);
      }
    }
    const refresh = () => { void load(); };
    refresh();
    window.addEventListener('conversation:changed', refresh);
    return () => {
      active = false;
      window.removeEventListener('conversation:changed', refresh);
    };
  }, [session?.user.id]);

  /** 删除会话（后端软删除）：删完刷新列表；删的正是当前打开的那条时回到新对话。 */
  function confirmDelete(task: Conversation) {
    modal.confirm({
      title: '删除任务',
      content: `「${task.title || '新对话'}」会从任务列表与会话历史中移除，期间生成的报告也一并不可再打开。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteConversation(task.id);
        } catch (error) {
          message.error(error instanceof Error ? error.message : '删除失败，请重试');
          throw error;
        }
        message.success('已删除');
        window.dispatchEvent(new CustomEvent('conversation:deleted', { detail: { id: task.id } }));
        notifyConversationChanged();
        if (String(task.id) === activeId) {
          window.dispatchEvent(new Event('chat:new-task'));
          history.push('/chat');
        }
      },
    });
  }

  if (!session) return null;
  const groups = recentGroups(tasks);
  return <div className="pro-task-menu">
    <Button className="pro-task-new" block icon={<PlusOutlined />} onClick={() => {
      window.dispatchEvent(new Event('chat:new-task'));
      history.push('/chat');
    }}>新建任务</Button>
    <div className="pro-task-heading">
      <span>最近任务</span>
      <Tooltip title="查看全部任务"><Link to="/conversations" aria-label="查看全部任务"><RightOutlined /></Link></Tooltip>
    </div>
    {groups.length ? <Collapse ghost size="small" expandIconPlacement="end"
      activeKey={groups.filter(group => openByDate[group.key] ?? group.openByDefault).map(group => group.key)}
      onChange={keys => setOpenByDate(Object.fromEntries(groups.map(group => [group.key, keys.includes(group.key)])))}
      items={groups.map(group => ({
        key: group.key,
        label: group.label,
        children: <Conversations aria-label={`${group.label}的会话`} activeKey={activeId}
          items={group.tasks.map(task => ({
            key: String(task.id),
            label: <span className="pro-task-label">
              <span className="pro-task-title" title={task.title || '新对话'}>{task.title || '新对话'}</span>
              {timeLabel(task) ? <time className="pro-task-time" dateTime={task.update_time || task.create_time}>{timeLabel(task)}</time> : null}
            </span>,
          }))}
          menu={item => ({
            items: [{ key: 'delete', label: '删除任务', danger: true, icon: <DeleteOutlined /> }],
            onClick: ({ key, domEvent }) => {
              domEvent.stopPropagation();
              if (key !== 'delete') return;
              const task = group.tasks.find(entry => String(entry.id) === item.key);
              if (task) confirmDelete(task);
            },
          })}
          onActiveChange={id => history.push(`/chat?conversation_id=${encodeURIComponent(id)}`)} />,
      }))} /> : <div className="pro-task-empty">近 5 天暂无会话</div>}
    <div className="pro-sider-gap" aria-hidden="true" />
  </div>;
}

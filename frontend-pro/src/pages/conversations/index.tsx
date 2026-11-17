import { ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { DeleteOutlined } from '@ant-design/icons';
import { App, Button, Popconfirm } from 'antd';
import { Link } from '@umijs/max';
import { useEffect, useRef } from 'react';
import { deleteConversation, listConversations } from '@/api/adapter/chatAdapter';
import type { Conversation } from '@/api/adapter/chatAdapter';
import { CONVERSATION_CHANGED_EVENT, notifyConversationChanged } from '@/utils/conversationTitle';

export default function ConversationsPage() {
  const actionRef = useRef<ActionType>(null);
  const { message } = App.useApp();
  // 侧栏删除任务后，这里也跟着刷新，避免列表和实际数据不一致。
  useEffect(() => {
    const reload = () => { actionRef.current?.reload(); };
    window.addEventListener(CONVERSATION_CHANGED_EVENT, reload);
    return () => window.removeEventListener(CONVERSATION_CHANGED_EVENT, reload);
  }, []);
  return <ProTable<Conversation> headerTitle="全部任务" rowKey="id" search={false}
    actionRef={actionRef}
    request={async () => ({ data: await listConversations(), success: true })}
    pagination={{ pageSize: 10, showSizeChanger: false }}
    columns={[
      { title: '会话', dataIndex: 'title', render: (_, row) => <Link to={`/chat?conversation_id=${row.id}`}>{row.title || '新对话'}</Link> },
      { title: '更新时间', dataIndex: 'update_time', valueType: 'dateTime' },
      {
        title: '操作', valueType: 'option', width: 96,
        render: (_, row) => <Popconfirm
          title="删除任务"
          description="会从任务列表与会话历史中移除，期间生成的报告也一并不可再打开。"
          okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
          onConfirm={async () => {
            try {
              await deleteConversation(row.id);
            } catch (error) {
              message.error(error instanceof Error ? error.message : '删除失败，请重试');
              return;
            }
            message.success('已删除');
            window.dispatchEvent(new CustomEvent('conversation:deleted', { detail: { id: row.id } }));
            notifyConversationChanged();
          }}>
          <Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>,
      },
    ]} />;
}

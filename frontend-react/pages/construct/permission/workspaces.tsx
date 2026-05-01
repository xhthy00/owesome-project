import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  UserAddOutlined
} from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { systemApi, type Workspace, type WorkspaceMember } from "@/api/system";

export default function PermissionWorkspacesPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<number>(1);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [memberKeyword, setMemberKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [openWorkspaceModal, setOpenWorkspaceModal] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<Workspace | null>(null);
  const [openMemberModal, setOpenMemberModal] = useState(false);
  const [candidateKeyword, setCandidateKeyword] = useState("");
  const [candidateUsers, setCandidateUsers] = useState<Array<{ id: number; name: string; account: string }>>([]);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<number[]>([]);
  const [newMemberWeight, setNewMemberWeight] = useState<number>(0);
  const [form] = Form.useForm<{ name: string }>();

  const loadWorkspaces = async () => {
    const data = await systemApi.listWorkspaces();
    setWorkspaces(data);
    if (!data.some((item) => item.id === activeWorkspaceId) && data.length) {
      setActiveWorkspaceId(data[0].id);
    }
  };

  const loadMembers = async (oid = activeWorkspaceId, keyword = memberKeyword) => {
    const data = keyword
      ? await systemApi.searchWorkspaceMembers(oid, keyword, 1, 100)
      : await systemApi.pagerWorkspaceMembers(oid, 1, 100);
    setMembers(data.items);
  };

  const loadAll = async () => {
    setLoading(true);
    try {
      await loadWorkspaces();
      await loadMembers();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载数据失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    void loadMembers(activeWorkspaceId);
  }, [activeWorkspaceId]);

  const activeWorkspace = useMemo(
    () => workspaces.find((item) => item.id === activeWorkspaceId),
    [workspaces, activeWorkspaceId]
  );

  const memberColumns: ColumnsType<WorkspaceMember> = [
    { title: "名称", dataIndex: "name", width: 150 },
    { title: "账号", dataIndex: "account", width: 160 },
    { title: "邮箱", dataIndex: "email", width: 220 },
    {
      title: "成员类型",
      dataIndex: "weight",
      width: 180,
      render: (_, row) => (
        <Select
          value={row.weight}
          options={[
            { label: "普通成员", value: 0 },
            { label: "管理员", value: 1 }
          ]}
          style={{ width: 140 }}
          onChange={async (value) => {
            await systemApi.updateWorkspaceMemberType(row.uid, activeWorkspaceId, value);
            message.success("成员类型已更新");
            await loadMembers(activeWorkspaceId);
          }}
        />
      )
    },
    {
      title: "操作",
      width: 90,
      render: (_, row) => (
        <Popconfirm
          title={`移除成员 ${row.name} ?`}
          onConfirm={async () => {
            await systemApi.removeWorkspaceMembers([row.uid], activeWorkspaceId);
            message.success("移除成功");
            await loadMembers(activeWorkspaceId);
          }}
        >
          <Button type="link" danger>
            移除
          </Button>
        </Popconfirm>
      )
    }
  ];

  const searchCandidates = async () => {
    const res = await systemApi.workspaceOptionPager(activeWorkspaceId, 1, 100, candidateKeyword);
    setCandidateUsers(res.items);
  };

  const workspaceActions = (row: Workspace) => (
    <Space size={8}>
      <Button
        size="small"
        icon={<EditOutlined />}
        onClick={() => {
          setEditingWorkspace(row);
          form.setFieldsValue({ name: row.name });
          setOpenWorkspaceModal(true);
        }}
      />
      {row.id !== 1 ? (
        <Popconfirm
          title={`删除工作空间 ${row.name} ?`}
          onConfirm={async () => {
            await systemApi.deleteWorkspace(row.id);
            message.success("删除成功");
            await loadAll();
          }}
        >
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ) : null}
    </Space>
  );

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            工作空间管理
          </Typography.Title>
          <Typography.Text className="oc-muted">与 SQLBot 一致：左侧工作空间，右侧成员管理</Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void loadAll()} loading={loading}>
            刷新
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setEditingWorkspace(null);
              form.resetFields();
              setOpenWorkspaceModal(true);
            }}
          >
            新增工作空间
          </Button>
        </Space>
      </div>

      <div className="grid grid-cols-[300px_1fr] gap-4">
        <div className="rounded-2xl border border-[#e5e7eb] bg-white p-3 shadow-sm">
          <div className="mb-2 text-sm font-medium">工作空间</div>
          <div className="max-h-[560px] overflow-auto">
            {workspaces.map((item) => (
              <div
                key={item.id}
                className={`mb-2 flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 ${
                  item.id === activeWorkspaceId ? "bg-blue-50 text-blue-600" : "hover:bg-[#f5f7fb]"
                }`}
                onClick={() => setActiveWorkspaceId(item.id)}
              >
                <span className="truncate">{item.name}</span>
                {workspaceActions(item)}
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-2xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-base font-medium">
              {activeWorkspace?.name || "-"} 成员 ({members.length})
            </div>
            <Space>
              <Input
                allowClear
                value={memberKeyword}
                onChange={(e) => setMemberKeyword(e.target.value)}
                onPressEnter={() => void loadMembers(activeWorkspaceId, memberKeyword)}
                placeholder="搜索姓名/账号/邮箱"
                prefix={<SearchOutlined />}
                style={{ width: 240 }}
              />
              <Button
                icon={<UserAddOutlined />}
                type="primary"
                onClick={async () => {
                  setSelectedCandidateIds([]);
                  setCandidateKeyword("");
                  setOpenMemberModal(true);
                  await searchCandidates();
                }}
              >
                添加成员
              </Button>
            </Space>
          </div>

          <Table
            rowKey="id"
            columns={memberColumns}
            dataSource={members}
            loading={loading}
            pagination={false}
          />
        </div>
      </div>

      <Modal
        title={editingWorkspace ? "重命名工作空间" : "新增工作空间"}
        open={openWorkspaceModal}
        onCancel={() => setOpenWorkspaceModal(false)}
        onOk={() => {
          form
            .validateFields()
            .then(async (values) => {
              if (editingWorkspace) {
                await systemApi.updateWorkspace(editingWorkspace.id, values.name);
              } else {
                await systemApi.createWorkspace(values.name);
              }
              message.success("创建成功");
              setOpenWorkspaceModal(false);
              form.resetFields();
              await loadAll();
            })
            .catch(() => void 0);
        }}
      >
        <Form layout="vertical" form={form}>
          <Form.Item name="name" label="工作空间名称" rules={[{ required: true }]}>
            <Input placeholder="请输入工作空间名称" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="添加成员"
        open={openMemberModal}
        onCancel={() => setOpenMemberModal(false)}
        onOk={async () => {
          await systemApi.addWorkspaceMembers(selectedCandidateIds, activeWorkspaceId, newMemberWeight);
          message.success("添加成功");
          setOpenMemberModal(false);
          await loadMembers(activeWorkspaceId);
        }}
      >
        <Space direction="vertical" className="w-full" size={12}>
          <Input
            placeholder="搜索候选用户"
            value={candidateKeyword}
            onChange={(e) => setCandidateKeyword(e.target.value)}
            onPressEnter={() => void searchCandidates()}
            prefix={<SearchOutlined />}
          />
          <Select
            value={newMemberWeight}
            onChange={setNewMemberWeight}
            style={{ width: "100%" }}
            options={[
              { label: "普通成员", value: 0 },
              { label: "管理员", value: 1 }
            ]}
          />
          <Select
            mode="multiple"
            value={selectedCandidateIds}
            onChange={setSelectedCandidateIds}
            style={{ width: "100%" }}
            placeholder="选择要添加的成员"
            options={candidateUsers.map((u) => ({
              label: `${u.name} (${u.account})`,
              value: u.id
            }))}
          />
        </Space>
      </Modal>
    </div>
  );
}

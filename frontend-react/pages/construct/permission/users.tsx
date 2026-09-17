import {
  DeleteOutlined,
  DownloadOutlined,
  PlusOutlined,
  ReloadOutlined,
  UploadOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Divider,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { UploadProps } from "antd";
import { useEffect, useState } from "react";
import { getCurrentUser } from "@/api/auth";
import { getAccessToken } from "@/auth/session";
import { getApiBaseUrl } from "@/api/client";
import { systemApi, type SystemUser } from "@/api/system";

const WORKSPACE_OID_KEY = "frontend_react_workspace_oid";

function isSuperUser(row: SystemUser) {
  return row.sys_role === "super_admin" || (row.id === 1 && row.account === "admin");
}

function isPlatformUser(row: SystemUser) {
  return isSuperUser(row) || row.sys_role === "platform_admin";
}

function isProtectedUser(row: SystemUser, currentUserId?: number, isSuperAdmin?: boolean) {
  if (currentUserId != null && row.id === currentUserId) return true;
  if (isSuperUser(row)) return true;
  if (isPlatformUser(row) && !isSuperAdmin) return true;
  return false;
}

export default function PermissionUsersPage() {
  const [rows, setRows] = useState<SystemUser[]>([]);
  const [total, setTotal] = useState(0);
  const [pageNum, setPageNum] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [currentUserId, setCurrentUserId] = useState<number>();
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [csvText, setCsvText] = useState("");
  const [batchResult, setBatchResult] = useState("");
  const [form] = Form.useForm<{ account: string; name: string; email?: string; password?: string }>();

  const load = async (page = pageNum, size = pageSize) => {
    setLoading(true);
    try {
      const res = await systemApi.pagerUsers(page, size);
      const lastPage = Math.max(1, Math.ceil((res.total || 0) / size) || 1);
      if (page > lastPage) {
        const again = await systemApi.pagerUsers(lastPage, size);
        setRows(again.items);
        setTotal(again.total);
        setPageNum(lastPage);
      } else {
        setRows(res.items);
        setTotal(res.total);
        setPageNum(page);
      }
      setPageSize(size);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载用户失败");
    } finally {
      setLoading(false);
    }
  };

  const loadLastPage = async (size = pageSize) => {
    setLoading(true);
    try {
      const first = await systemApi.pagerUsers(1, size);
      const lastPage = Math.max(1, Math.ceil((first.total || 0) / size) || 1);
      const res = lastPage === 1 ? first : await systemApi.pagerUsers(lastPage, size);
      setRows(res.items);
      setTotal(res.total);
      setPageNum(lastPage);
      setPageSize(size);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载用户失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void getCurrentUser()
      .then((u) => {
        setCurrentUserId(u.id);
        setIsSuperAdmin(Boolean(u.is_super_admin));
      })
      .catch(() => {
        setCurrentUserId(undefined);
        setIsSuperAdmin(false);
      });
    void load(1, 20);
    // 仅首次进入拉取第一页
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const confirmDelete = async (row: SystemUser) => {
    setLoading(true);
    try {
      await systemApi.deleteUser(row.id);
      message.success("用户已删除");
      const nextPage = rows.length === 1 && pageNum > 1 ? pageNum - 1 : pageNum;
      await load(nextPage, pageSize);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
      setLoading(false);
    }
  };

  const confirmSetSysRole = async (row: SystemUser, sys_role: "platform_admin" | "") => {
    setLoading(true);
    try {
      await systemApi.setUserSysRole(row.id, sys_role);
      message.success(sys_role ? "已设为管理员" : "已取消管理员");
      await load(pageNum, pageSize);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "操作失败");
      setLoading(false);
    }
  };

  const downloadTemplate = async () => {
    const token = getAccessToken();
    const wsOid =
      typeof window !== "undefined" ? window.localStorage.getItem(WORKSPACE_OID_KEY) : null;
    const resp = await fetch(`${getApiBaseUrl()}/user/csv-template`, {
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(wsOid ? { "X-Workspace-Oid": wsOid.trim() } : {})
      }
    });
    if (!resp.ok) {
      message.error("下载模板失败");
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "user_import_template.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const uploadProps: UploadProps = {
    accept: ".csv,text/csv",
    showUploadList: false,
    beforeUpload: (file) => {
      if (/\.xlsx?$/i.test(file.name)) {
        message.error("请另存为 CSV UTF-8，不要上传 Excel 工作簿");
        return false;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const raw = String(reader.result || "");
        if (raw.startsWith("PK")) {
          message.error("这是 Excel 工作簿，请另存为 CSV UTF-8 后再导入");
          return;
        }
        setCsvText(raw.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n"));
        setBatchResult("");
      };
      reader.readAsText(file, "UTF-8");
      return false;
    }
  };

  const runBatchCreate = async () => {
    if (!csvText.trim()) {
      message.warning("请先上传 CSV 或粘贴内容");
      return;
    }
    setLoading(true);
    try {
      const res = await systemApi.batchCreateUsers({ csv: csvText });
      const failLines = res.failed.map((f) => `行 ${f.row} ${f.account}: ${f.reason}`);
      setBatchResult(`成功 ${res.success} 条，失败 ${res.failed.length} 条\n${failLines.join("\n")}`);
      if (res.success === 0) {
        message.error(
          res.failed.length ? `没有导入成功（失败 ${res.failed.length} 条）` : "CSV 没有有效数据"
        );
      } else if (res.failed.length) {
        message.warning(`成功 ${res.success} 条，失败 ${res.failed.length} 条（初始密码 123456）`);
        await loadLastPage(pageSize);
      } else {
        message.success(`成功导入 ${res.success} 条，初始密码 123456`);
        await loadLastPage(pageSize);
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "批量导入失败");
    } finally {
      setLoading(false);
    }
  };

  const columns: ColumnsType<SystemUser> = [
    { title: "ID", dataIndex: "id", width: 90 },
    { title: "账号", dataIndex: "account", width: 180 },
    { title: "名称", dataIndex: "name", width: 160 },
    { title: "邮箱", dataIndex: "email", width: 220 },
    {
      title: "教育角色",
      dataIndex: "edu_role",
      width: 140,
      render: (v: string | null | undefined) =>
        v ? <Tag color="geekblue">{v}</Tag> : <Tag>未配置</Tag>
    },
    {
      title: "角色",
      dataIndex: "sys_role",
      width: 120,
      render: (_, row) => {
        if (isSuperUser(row)) return <Tag color="red">超管</Tag>;
        if (row.sys_role === "platform_admin") return <Tag color="volcano">管理员</Tag>;
        return <Tag color={row.status ? "blue" : "default"}>{row.status ? "member" : "disabled"}</Tag>;
      }
    },
    { title: "工作空间", dataIndex: "oid", width: 100 },
    {
      title: "操作",
      key: "actions",
      width: 220,
      fixed: "right",
      render: (_, row) => {
        const canAppoint = isSuperAdmin && !isSuperUser(row) && row.id !== currentUserId;
        const canDelete = !isProtectedUser(row, currentUserId, isSuperAdmin);
        if (!canAppoint && !canDelete) {
          return (
            <Typography.Text type="secondary" className="text-xs">
              —
            </Typography.Text>
          );
        }
        return (
          <Space size={0}>
            {canAppoint ? (
              row.sys_role === "platform_admin" ? (
                <Popconfirm
                  title="取消管理员"
                  description={`取消「${row.name}」的平台管理员权限？`}
                  okText="确定"
                  cancelText="取消"
                  onConfirm={() => void confirmSetSysRole(row, "")}
                >
                  <Button type="link" size="small">
                    取消管理员
                  </Button>
                </Popconfirm>
              ) : (
                <Popconfirm
                  title="设为管理员"
                  description={`将「${row.name}」设为平台管理员？功能与超管一致，但不能任命其他管理员。`}
                  okText="确定"
                  cancelText="取消"
                  onConfirm={() => void confirmSetSysRole(row, "platform_admin")}
                >
                  <Button type="link" size="small">
                    设为管理员
                  </Button>
                </Popconfirm>
              )
            ) : null}
            {canDelete ? (
              <Popconfirm
                title="删除用户"
                description={`确定删除「${row.name}」（${row.account}）？删除后无法登录。`}
                okText="删除"
                okType="danger"
                cancelText="取消"
                onConfirm={() => void confirmDelete(row)}
              >
                <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            ) : null}
          </Space>
        );
      }
    }
  ];

  return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto p-6 pb-10">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            用户管理
          </Typography.Title>
          <Typography.Text className="oc-muted">对齐 SQLBot 用户管理能力</Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load(pageNum, pageSize)} loading={loading}>
            刷新
          </Button>
          <Button
            icon={<UploadOutlined />}
            onClick={() => {
              setCsvText("");
              setBatchResult("");
              setBatchOpen(true);
            }}
          >
            批量导入
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新增用户
          </Button>
        </Space>
      </div>

      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={rows}
          loading={loading}
          scroll={{ x: 1100 }}
          pagination={{
            current: pageNum,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50],
            showTotal: (n) => `共 ${n} 人`,
            onChange: (page, size) => void load(page, size)
          }}
        />
      </div>

      <Modal
        title="新增用户"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => {
          form
            .validateFields()
            .then(async (values) => {
              await systemApi.createUser(values);
              message.success("创建成功");
              setOpen(false);
              form.resetFields();
              await loadLastPage(pageSize);
            })
            .catch(() => void 0);
        }}
      >
        <Form layout="vertical" form={form}>
          <Form.Item name="account" label="账号" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input />
          </Form.Item>
          <Form.Item name="password" label="初始密码" extra="不填则默认为 123456">
            <Input.Password placeholder="123456" />
          </Form.Item>
          <Form.Item name="oid" label="工作空间" initialValue={1}>
            <Select options={[{ label: "默认工作空间(1)", value: 1 }]} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="批量导入用户"
        open={batchOpen}
        onCancel={() => setBatchOpen(false)}
        width={720}
        footer={
          <Space>
            <Button onClick={() => setBatchOpen(false)}>关闭</Button>
            <Button type="primary" loading={loading} onClick={() => void runBatchCreate()}>
              执行导入
            </Button>
          </Space>
        }
        destroyOnClose
      >
        <Alert
          className="mb-3"
          type="info"
          showIcon
          message="必填 account、name；email / oid 可空（oid 默认 1）。初始密码固定为 123456。Excel 请另存为 CSV UTF-8，不要只改后缀。"
        />
        <Space className="mb-4">
          <Button icon={<DownloadOutlined />} onClick={() => void downloadTemplate()}>
            下载 CSV 模板
          </Button>
          <Upload {...uploadProps}>
            <Button icon={<UploadOutlined />}>上传 CSV</Button>
          </Upload>
        </Space>
        <Input.TextArea
          rows={10}
          value={csvText}
          onChange={(e) => setCsvText(e.target.value)}
          placeholder={"account,name,email,oid\njiaoyan_01,教研01,,1"}
        />
        {batchResult ? (
          <>
            <Divider className="!my-3" />
            <pre className="max-h-60 overflow-auto rounded bg-[#f8fafc] p-3 text-xs whitespace-pre-wrap dark:bg-[#0f172a]">
              {batchResult}
            </pre>
          </>
        ) : null}
      </Modal>
    </div>
  );
}

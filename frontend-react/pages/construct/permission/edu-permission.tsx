import {
  ClusterOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
  UploadOutlined,
  UserOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message
} from "antd";
import type { UploadProps } from "antd";
import { useEffect, useMemo, useState } from "react";
import { getAccessToken } from "@/auth/session";
import { getApiBaseUrl } from "@/api/client";
import { permissionApi, type EduRoleItem } from "@/api/permission";
import { systemApi, type SystemUser } from "@/api/system";

const WORKSPACE_OID_KEY = "frontend_react_workspace_oid";

type ScopeForm = {
  edu_role: string;
  school_id?: string;
  school_name?: string;
  class_names?: string;
  student_id?: string;
};

function roleTagClass(code: string): string {
  switch (code) {
    case "bureau_admin":
      return "bg-[#f3e8ff] text-[#7e22ce] dark:bg-[#581c87]/40 dark:text-[#e9d5ff]";
    case "school_admin":
      return "bg-[#dbeafe] text-[#1d4ed8] dark:bg-[#1e3a8a]/50 dark:text-[#bfdbfe]";
    case "teacher":
      return "bg-[#dcfce7] text-[#166534] dark:bg-[#14532d]/40 dark:text-[#bbf7d0]";
    case "student":
      return "bg-[#ffedd5] text-[#c2410c] dark:bg-[#7c2d12]/40 dark:text-[#fed7aa]";
    default:
      return "bg-[#f1f5f9] text-[#475569] dark:bg-[#334155] dark:text-[#cbd5e1]";
  }
}

export default function EduPermissionPage() {
  const [roles, setRoles] = useState<EduRoleItem[]>([]);
  const [users, setUsers] = useState<SystemUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewText, setPreviewText] = useState("");
  const [editingUser, setEditingUser] = useState<SystemUser | null>(null);
  const [selectedUserIds, setSelectedUserIds] = useState<number[]>([]);
  const [csvText, setCsvText] = useState("");
  const [batchResult, setBatchResult] = useState("");
  const [form] = Form.useForm<ScopeForm>();

  const selectedRole = Form.useWatch("edu_role", form);

  const roleOptions = useMemo(
    () => roles.map((r) => ({ value: r.code, label: r.label || r.code })),
    [roles]
  );

  const configuredUsers = useMemo(
    () => users.filter((u) => u.edu_role),
    [users]
  );

  const roleLabel = (code: string) =>
    roles.find((r) => r.code === code)?.label || code;

  const userOptionLabel = (u: SystemUser) => {
    if (!u.edu_role) return `${u.account} (${u.name})`;
    const role = u.edu_role_label || roleLabel(u.edu_role);
    const scopeHint =
      u.class_names?.length
        ? ` · ${u.class_names.join("、")}`
        : u.school_id
          ? ` · ${u.school_id}`
          : "";
    return `${u.account} (${u.name}) · ${role}${scopeHint}`;
  };

  const fillFormFromScope = (scope: Awaited<ReturnType<typeof permissionApi.getUserEduScope>>) => {
    form.setFieldsValue({
      edu_role: scope.edu_role || undefined,
      school_id: scope.school_id,
      class_names: (scope.class_names || []).join("|"),
      student_id: scope.student_id
    });
  };

  const loadUserScopeIntoForm = async (userId: number) => {
    setLoading(true);
    try {
      const scope = await permissionApi.getUserEduScope(userId);
      fillFormFromScope(scope);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载用户权限失败");
    } finally {
      setLoading(false);
    }
  };

  const handleUserSelectionChange = (ids: number[]) => {
    setSelectedUserIds(ids);
    if (ids.length === 1) {
      void loadUserScopeIntoForm(ids[0]);
    } else if (ids.length === 0) {
      form.resetFields();
    }
  };

  const loadMeta = async () => {
    setLoading(true);
    try {
      const [roleList, userPage] = await Promise.all([
        permissionApi.listEduRoles(),
        systemApi.pagerUsers(1, 500)
      ]);
      setRoles(roleList);
      setUsers(userPage.items);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadMeta();
  }, []);

  const openAdd = () => {
    setEditingUser(null);
    setSelectedUserIds([]);
    form.resetFields();
    setFormOpen(true);
  };

  const openEdit = async (user: SystemUser) => {
    setEditingUser(user);
    setSelectedUserIds([user.id]);
    setFormOpen(true);
    await loadUserScopeIntoForm(user.id);
  };

  const saveScope = async () => {
    if (selectedUserIds.length === 0) {
      message.warning("请至少选择一位用户");
      return;
    }
    try {
      const values = await form.validateFields();
      const classNames = (values.class_names || "")
        .split(/[|,，]/)
        .map((s) => s.trim())
        .filter(Boolean);
      const payload = {
        edu_role: values.edu_role,
        school_id: values.school_id,
        class_names: classNames.length ? classNames : undefined,
        student_id: values.student_id
      };
      setLoading(true);
      await Promise.all(selectedUserIds.map((uid) => permissionApi.updateUserEduScope(uid, payload)));
      message.success(
        selectedUserIds.length === 1
          ? "教育权限已保存"
          : `已为 ${selectedUserIds.length} 位用户保存教育权限`
      );
      setFormOpen(false);
      void loadMeta();
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const confirmDeleteScope = (user: SystemUser) => {
    Modal.confirm({
      title: "删除教育权限",
      content: `确定移除用户「${user.name}」（${user.account}）的教育数据范围？删除后该用户将不再受 edu_role 行级过滤约束。`,
      okText: "删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        setLoading(true);
        try {
          await permissionApi.deleteUserEduScope(user.id);
          message.success("教育权限已删除");
          if (editingUser?.id === user.id) {
            setFormOpen(false);
            setEditingUser(null);
            setSelectedUserIds([]);
            form.resetFields();
          }
          void loadMeta();
        } catch (err) {
          message.error(err instanceof Error ? err.message : "删除失败");
        } finally {
          setLoading(false);
        }
      }
    });
  };

  const previewEffective = async (userId: number) => {
    try {
      const sampleSql =
        "SELECT sc.student_id, sc.class, sc.score FROM tb_score sc JOIN tb_school sch ON sc.school_id = sch.id LIMIT 10";
      const res = await permissionApi.previewEduEffective({
        user_id: userId,
        sql: sampleSql,
        datasource_id: 1
      });
      setPreviewText(JSON.stringify(res, null, 2));
      setPreviewOpen(true);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "预览失败");
    }
  };

  const downloadTemplate = async () => {
    const token = getAccessToken();
    const wsOid =
      typeof window !== "undefined" ? window.localStorage.getItem(WORKSPACE_OID_KEY) : null;
    const resp = await fetch(`${getApiBaseUrl()}/permission/edu/template`, {
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
    a.download = "edu_permission_template.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const uploadProps: UploadProps = {
    accept: ".csv,text/csv",
    showUploadList: false,
    beforeUpload: (file) => {
      const reader = new FileReader();
      reader.onload = () => {
        setCsvText(String(reader.result || ""));
        setBatchResult("");
      };
      reader.readAsText(file);
      return false;
    }
  };

  const runBatchBind = async () => {
    if (!csvText.trim()) {
      message.warning("请先上传 CSV 或粘贴内容");
      return;
    }
    setLoading(true);
    try {
      const res = await permissionApi.batchBindEduScope({ csv: csvText });
      setBatchResult(
        `成功 ${res.success} 条，失败 ${res.failed.length} 条\n` +
          res.failed.map((f) => `行 ${f.row} ${f.account}: ${f.reason}`).join("\n")
      );
      message.success("批量绑定完成");
      void loadMeta();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "批量绑定失败");
    } finally {
      setLoading(false);
    }
  };

  const requiredFields = roles.find((r) => r.code === selectedRole)?.required_fields || [];

  const scopeItemCount = (user: SystemUser) => {
    let n = 0;
    if (user.school_id) n += 1;
    if (user.class_names?.length) n += user.class_names.length;
    if (user.student_id) n += 1;
    return n;
  };

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            教育权限
          </Typography.Title>
          <Typography.Text className="oc-muted">
            教育局 / 校长 / 老师 / 学生 四级数据范围，与数据权限规则叠加生效
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void loadMeta()} loading={loading}>
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
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
            添加教育权限
          </Button>
        </Space>
      </div>

      <Alert
        className="mb-4"
        type="info"
        showIcon
        message="权限说明：按用户配置 edu_role 及学校/班级/学号范围；保存后教育问数 SQL 将自动注入对应行级过滤。"
      />

      {configuredUsers.length === 0 && !loading ? (
        <Empty className="py-16" description="暂无已配置的教育权限" />
      ) : (
        <Row gutter={[16, 16]}>
          {configuredUsers.map((user) => {
            const roleCode = user.edu_role || "";
            const classList = user.class_names || [];
            const scopeTags: Array<{ key: string; label: string; tone: "row" | "column" | "role" }> = [
              { key: "role", label: user.edu_role_label || roleLabel(roleCode), tone: "role" }
            ];
            if (user.school_id) {
              scopeTags.push({
                key: "school",
                label: user.school_id,
                tone: "column"
              });
            }
            classList.forEach((c) => scopeTags.push({ key: `class-${c}`, label: c, tone: "row" }));
            if (user.student_id) {
              scopeTags.push({ key: "student", label: user.student_id, tone: "column" });
            }

            return (
              <Col key={user.id} xs={24} md={12} lg={8}>
                <Card
                  variant="borderless"
                  className="h-full overflow-hidden rounded-2xl border border-[#e2e8f0] bg-gradient-to-br from-white via-[#fafcff] to-[#f1f6ff] shadow-[0_2px_14px_rgba(15,23,42,0.06)] transition-all duration-200 hover:-translate-y-0.5 hover:border-[#93c5fd] hover:shadow-[0_10px_28px_rgba(37,99,235,0.12)] dark:border-[#334155] dark:from-[#141923] dark:via-[#11161f] dark:to-[#0f141c] dark:hover:border-[#3b82f6]/50"
                  styles={{
                    header: {
                      borderBottom: "1px solid rgba(226, 232, 240, 0.9)",
                      padding: "14px 16px",
                      minHeight: 56
                    },
                    body: { padding: "14px 16px 16px" }
                  }}
                  title={
                    <div className="flex min-w-0 flex-col gap-0.5 pr-2">
                      <Typography.Text
                        ellipsis={{ tooltip: user.name }}
                        className="text-[15px] font-semibold leading-snug text-[#0f172a] dark:text-[#f1f5f9]"
                      >
                        {user.name}
                      </Typography.Text>
                      <span className="text-[11px] font-medium tracking-wide text-[#94a3b8] dark:text-[#64748b]">
                        用户 #{user.id} · {user.account}
                      </span>
                    </div>
                  }
                  extra={
                    <Space
                      size={0}
                      wrap
                      className="max-w-[min(100%,11rem)] justify-end sm:max-w-none"
                      split={<span className="mx-0.5 text-[#cbd5e1] dark:text-[#475569]">|</span>}
                    >
                      <Button
                        type="link"
                        size="small"
                        icon={<EditOutlined className="text-[13px]" />}
                        className="!px-1.5 text-[12px] font-medium text-[#475569] hover:text-[#2563eb] dark:text-[#94a3b8] dark:hover:text-[#93c5fd]"
                        onClick={() => void openEdit(user)}
                      >
                        编辑权限
                      </Button>
                      <Button
                        type="link"
                        size="small"
                        icon={<EyeOutlined className="text-[13px]" />}
                        className="!px-1.5 text-[12px] font-medium text-[#475569] hover:text-[#2563eb] dark:text-[#94a3b8] dark:hover:text-[#93c5fd]"
                        onClick={() => void previewEffective(user.id)}
                      >
                        预览生效
                      </Button>
                      <Button
                        type="link"
                        size="small"
                        danger
                        icon={<DeleteOutlined className="text-[13px]" />}
                        className="!px-1.5 text-[12px] font-medium"
                        onClick={() => confirmDeleteScope(user)}
                      >
                        删除
                      </Button>
                      <Tooltip title="编辑教育权限">
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined />}
                          className="text-[#64748b] hover:bg-[#eff6ff] hover:text-[#2563eb] dark:hover:bg-[#1e293b]"
                          onClick={() => void openEdit(user)}
                        />
                      </Tooltip>
                    </Space>
                  }
                >
                  <div className="mb-3 grid grid-cols-2 gap-3">
                    <div className="flex items-center gap-2.5 rounded-xl border border-[#e8eef5] bg-white/80 px-3 py-2.5 dark:border-[#2f3d52] dark:bg-[#0f172a]/60">
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#eff6ff] text-[#2563eb] dark:bg-[#1e3a5f] dark:text-[#93c5fd]">
                        <UserOutlined />
                      </span>
                      <div>
                        <div className="text-[11px] font-medium uppercase tracking-wide text-[#94a3b8]">绑定用户</div>
                        <div className="text-lg font-semibold tabular-nums leading-none text-[#0f172a] dark:text-[#e2e8f0]">
                          1
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2.5 rounded-xl border border-[#e8eef5] bg-white/80 px-3 py-2.5 dark:border-[#2f3d52] dark:bg-[#0f172a]/60">
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#f0fdf4] text-[#16a34a] dark:bg-[#14532d]/50 dark:text-[#86efac]">
                        <ClusterOutlined />
                      </span>
                      <div>
                        <div className="text-[11px] font-medium uppercase tracking-wide text-[#94a3b8]">范围项</div>
                        <div className="text-lg font-semibold tabular-nums leading-none text-[#0f172a] dark:text-[#e2e8f0]">
                          {scopeItemCount(user)}
                        </div>
                      </div>
                    </div>
                  </div>
                  <Divider className="!my-3 border-[#eef2f7] dark:border-[#2f3d52]" />
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-[#94a3b8] dark:text-[#64748b]">
                    数据范围
                  </div>
                  <Space wrap className="mt-2">
                    {scopeTags.map((item) => (
                      <Tag
                        key={`${user.id}-${item.key}`}
                        className={`m-0 rounded-full border-0 px-2.5 py-0.5 text-xs font-medium ${
                          item.tone === "role"
                            ? roleTagClass(roleCode)
                            : item.tone === "row"
                              ? "bg-[#dcfce7] text-[#166534] dark:bg-[#14532d]/40 dark:text-[#bbf7d0]"
                              : "bg-[#dbeafe] text-[#1d4ed8] dark:bg-[#1e3a8a]/50 dark:text-[#bfdbfe]"
                        }`}
                      >
                        {item.label}
                      </Tag>
                    ))}
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}

      <Modal
        title={editingUser ? "编辑教育权限" : "添加教育权限"}
        open={formOpen}
        onCancel={() => setFormOpen(false)}
        footer={
          <Space>
            {editingUser ? (
              <Button
                danger
                icon={<DeleteOutlined />}
                onClick={() => confirmDeleteScope(editingUser)}
              >
                删除权限
              </Button>
            ) : null}
            <Button onClick={() => setFormOpen(false)}>取消</Button>
            {selectedUserIds.length === 1 ? (
              <Button
                icon={<EyeOutlined />}
                onClick={() => void previewEffective(selectedUserIds[0])}
              >
                预览生效权限
              </Button>
            ) : null}
            <Button type="primary" loading={loading} onClick={() => void saveScope()}>
              保存
            </Button>
          </Space>
        }
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="mt-2">
          <Form.Item label="用户" required extra="选择已有权限的用户时将自动加载现有配置，可在班级等字段中继续追加">
            <Select
              mode="multiple"
              showSearch
              allowClear
              placeholder="选择一位或多位用户（含已配置用户，如为老师追加班级）"
              optionFilterProp="label"
              value={selectedUserIds}
              onChange={handleUserSelectionChange}
              options={users.map((u) => ({
                value: u.id,
                label: userOptionLabel(u)
              }))}
            />
          </Form.Item>
          {selectedUserIds.length > 1 ? (
            <Alert
              className="!mb-4"
              type="warning"
              showIcon
              message="当前权限配置将批量应用到所选全部用户，请确认范围字段（如班级、学号）是否适用于每位用户。"
            />
          ) : null}

          <Form.Item name="edu_role" label="教育角色" rules={[{ required: true, message: "请选择角色" }]}>
            <Select options={roleOptions} placeholder="选择角色" />
          </Form.Item>

          {(requiredFields.includes("school_id") || selectedRole === "bureau_admin") && (
            <>
              <Form.Item
                name="school_id"
                label="学校 ID"
                extra="填写学校名称的加密结果（密文 id），与 edu.tb_school.id 一致；平台不展示学校明文"
              >
                <Input placeholder="如 gz_2d2b5c7b" />
              </Form.Item>
            </>
          )}

          {requiredFields.includes("class_names") && (
            <Form.Item
              name="class_names"
              label="班级（多个用 | 分隔）"
              rules={[{ required: true, message: "请填写班级" }]}
            >
              <Input placeholder="高一(1)班|高一(2)班" />
            </Form.Item>
          )}

          {requiredFields.includes("student_id") && (
            <Form.Item
              name="student_id"
              label="学号"
              rules={[{ required: true, message: "请填写学号" }]}
            >
              <Input placeholder="STU20240002" />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title="批量导入教育权限"
        open={batchOpen}
        onCancel={() => setBatchOpen(false)}
        width={720}
        footer={
          <Space>
            <Button onClick={() => setBatchOpen(false)}>关闭</Button>
            <Button type="primary" loading={loading} onClick={() => void runBatchBind()}>
              执行批量绑定
            </Button>
          </Space>
        }
        destroyOnClose
      >
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
          placeholder="或直接粘贴 CSV 内容"
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

      <Modal
        title="生效权限预览"
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={<Button onClick={() => setPreviewOpen(false)}>关闭</Button>}
        width={720}
      >
        <pre className="max-h-[480px] overflow-auto rounded bg-[#f8fafc] p-3 text-xs dark:bg-[#0f172a]">
          {previewText}
        </pre>
      </Modal>
    </div>
  );
}

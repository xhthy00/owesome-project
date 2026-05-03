import {
  ClusterOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SettingOutlined,
  TeamOutlined,
  UserOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Steps,
  Tag,
  Tooltip,
  Typography,
  message
} from "antd";
import { useEffect, useMemo, useState } from "react";
import { datasourceApi, type DatasourceItem } from "@/api/datasource";
import { permissionApi, type PermissionGroup } from "@/api/permission";
import { systemApi } from "@/api/system";

type RuleForm = {
  id?: number;
  name: string;
  users: number[];
  permissions: Array<{
    name: string;
    type: "row" | "column";
    ds_id?: number;
    table_name?: string;
    expression_tree?: string;
    permissions?: string;
  }>;
};

type ColumnPermissionRow = {
  /** 元数据同步场景下的字段 ID；手动配置时可省略，运行时按 field_name 匹配 */
  field_id?: number;
  field_name: string;
  field_comment?: string;
  /** 与后端一致：false 表示隐藏该列；本页仅维护「需隐藏的列」，持久化时一律写 false */
  enable?: boolean;
};

/** 列权限为拒绝列表：每条均为隐藏列，加载旧数据时统一为 enable:false */
function normalizeColumnPermissionsJson(raw: unknown): string {
  try {
    const arr = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (!Array.isArray(arr)) return "[]";
    const out = arr
      .filter((x) => x && typeof x === "object")
      .map((x: Record<string, unknown>) => {
        const row = { ...x } as Record<string, unknown>;
        row.enable = false;
        return row;
      });
    return JSON.stringify(out);
  } catch {
    return "[]";
  }
}

function toJsonString(value: unknown, fallback: string) {
  if (typeof value === "string") return value;
  if (value == null) return fallback;
  try {
    return JSON.stringify(value);
  } catch {
    return fallback;
  }
}

export default function ConstructPermissionDataRulesPage() {
  const [list, setList] = useState<PermissionGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<PermissionGroup | null>(null);
  const [candidateUsers, setCandidateUsers] = useState<Array<{ id: number; name: string; account: string }>>([]);
  const [activeStep, setActiveStep] = useState(0);
  const [datasources, setDatasources] = useState<DatasourceItem[]>([]);
  const [form] = Form.useForm<RuleForm>();

  const buildFormValuesFromGroup = (group: PermissionGroup): RuleForm => {
    const groupUsers = Array.isArray(group.users) ? group.users.map((u) => Number(u)) : [];
    const groupPermissions = Array.isArray(group.permissions) ? group.permissions : [];
    return {
      id: group.id,
      name: group.name,
      users: groupUsers,
      permissions: groupPermissions.length
        ? groupPermissions.map((p, idx) => ({
            name: p.name || `rule_${idx + 1}`,
            type: p.type,
            ds_id: p.ds_id,
            table_name: p.table_name,
            expression_tree: toJsonString(p.expression_tree, "{}"),
            permissions:
              p.type === "column"
                ? normalizeColumnPermissionsJson(p.permissions)
                : toJsonString(p.permissions, "[]")
          }))
        : [{ name: "rule_1", type: "row", expression_tree: "{}", permissions: "[]" }]
    };
  };

  const searchUsers = async (keyword = "") => {
    const res = keyword
      ? await systemApi.searchWorkspaceMembers(1, keyword, 1, 500)
      : await systemApi.pagerWorkspaceMembers(1, 1, 500);
    const mapped = (res.items || []).map((item) => ({
      id: item.uid,
      name: item.name,
      account: item.account
    }));
    // 按 uid 去重，避免分页/接口差异导致重复项
    const uniq = Array.from(new Map(mapped.map((u) => [u.id, u])).values());
    setCandidateUsers(uniq);
  };

  const reload = async () => {
    setLoading(true);
    try {
      const res = await permissionApi.listPermissionGroups();
      setList(Array.isArray(res) ? res : []);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载权限规则失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    void datasourceApi.list({ limit: 500 }).then((res) => setDatasources(res.items || []));
  }, []);

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            数据权限
          </Typography.Title>
          <Typography.Text className="oc-muted">
            以规则组管理行权限/列权限，并绑定受限用户（仅作用于当前工作空间）
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void reload()} loading={loading}>
            刷新
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setEditing(null);
              form.setFieldsValue({
                name: "",
                users: [],
                permissions: [{ name: "rule_1", type: "row", expression_tree: "{}", permissions: "[]" }]
              });
              setActiveStep(0);
              setOpen(true);
              void searchUsers();
            }}
          >
            添加规则组
          </Button>
        </Space>
      </div>
      <Alert
        className="mb-4"
        type="info"
        showIcon
        message="权限说明：以规则组为单位；每组可包含多条行/列规则，并可绑定多个受限用户。"
      />
      <Row gutter={[16, 16]}>
        {list.map((group) => {
          const groupUsers = Array.isArray(group.users) ? group.users : [];
          const groupPermissions = Array.isArray(group.permissions) ? group.permissions : [];
          return (
          <Col key={group.id} xs={24} md={12} lg={8}>
            <Card
              bordered={false}
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
                    ellipsis={{ tooltip: group.name }}
                    className="text-[15px] font-semibold leading-snug text-[#0f172a] dark:text-[#f1f5f9]"
                  >
                    {group.name}
                  </Typography.Text>
                  <span className="text-[11px] font-medium tracking-wide text-[#94a3b8] dark:text-[#64748b]">
                    规则组 #{group.id}
                  </span>
                </div>
              }
              extra={
                <Space size={0} wrap className="max-w-[min(100%,11rem)] justify-end sm:max-w-none" split={<span className="mx-0.5 text-[#cbd5e1] dark:text-[#475569]">|</span>}>
                  <Button
                    type="link"
                    size="small"
                    icon={<SettingOutlined className="text-[13px]" />}
                    className="!px-1.5 text-[12px] font-medium text-[#475569] hover:text-[#2563eb] dark:text-[#94a3b8] dark:hover:text-[#93c5fd]"
                    onClick={() => {
                      setEditing(group);
                      form.setFieldsValue(buildFormValuesFromGroup(group));
                      setActiveStep(0);
                      setOpen(true);
                      void searchUsers();
                    }}
                  >
                    设置规则
                  </Button>
                  <Button
                    type="link"
                    size="small"
                    icon={<TeamOutlined className="text-[13px]" />}
                    className="!px-1.5 text-[12px] font-medium text-[#475569] hover:text-[#2563eb] dark:text-[#94a3b8] dark:hover:text-[#93c5fd]"
                    onClick={() => {
                      setEditing(group);
                      form.setFieldsValue(buildFormValuesFromGroup(group));
                      setActiveStep(1);
                      setOpen(true);
                      void searchUsers();
                    }}
                  >
                    设置用户
                  </Button>
                  <Tooltip title="编辑名称与说明">
                    <Button
                      type="text"
                      size="small"
                      icon={<EditOutlined />}
                      className="text-[#64748b] hover:bg-[#eff6ff] hover:text-[#2563eb] dark:hover:bg-[#1e293b]"
                      onClick={() => {
                        setEditing(group);
                        form.setFieldsValue(buildFormValuesFromGroup(group));
                        setActiveStep(0);
                        setOpen(true);
                        void searchUsers();
                      }}
                    />
                  </Tooltip>
                  <Popconfirm
                    title={`删除规则组 ${group.name} ?`}
                    description="删除后该组全部规则立即失效，相关成员恢复对应数据访问权限。"
                    onConfirm={async () => {
                      try {
                        await permissionApi.deletePermissionGroup(group.id);
                        message.success("删除成功");
                        await reload();
                      } catch (err) {
                        message.error(err instanceof Error ? err.message : "删除失败");
                      }
                    }}
                  >
                    <Tooltip title="删除规则组">
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} className="hover:bg-[#fef2f2]" />
                    </Tooltip>
                  </Popconfirm>
                </Space>
              }
            >
              <div className="mb-3 grid grid-cols-2 gap-3">
                <div className="flex items-center gap-2.5 rounded-xl border border-[#e8eef5] bg-white/80 px-3 py-2.5 dark:border-[#2f3d52] dark:bg-[#0f172a]/60">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#eff6ff] text-[#2563eb] dark:bg-[#1e3a5f] dark:text-[#93c5fd]">
                    <UserOutlined />
                  </span>
                  <div>
                    <div className="text-[11px] font-medium uppercase tracking-wide text-[#94a3b8]">受限用户</div>
                    <div className="text-lg font-semibold tabular-nums leading-none text-[#0f172a] dark:text-[#e2e8f0]">
                      {groupUsers.length}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2.5 rounded-xl border border-[#e8eef5] bg-white/80 px-3 py-2.5 dark:border-[#2f3d52] dark:bg-[#0f172a]/60">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#f0fdf4] text-[#16a34a] dark:bg-[#14532d]/50 dark:text-[#86efac]">
                    <ClusterOutlined />
                  </span>
                  <div>
                    <div className="text-[11px] font-medium uppercase tracking-wide text-[#94a3b8]">规则条数</div>
                    <div className="text-lg font-semibold tabular-nums leading-none text-[#0f172a] dark:text-[#e2e8f0]">
                      {groupPermissions.length}
                    </div>
                  </div>
                </div>
              </div>
              <Divider className="!my-3 border-[#eef2f7] dark:border-[#2f3d52]" />
              <div className="text-[11px] font-semibold uppercase tracking-wide text-[#94a3b8] dark:text-[#64748b]">规则类型</div>
              <Space wrap className="mt-2">
                {groupPermissions.map((item) => (
                  <Tag
                    key={`${group.id}-${item.id || item.name}`}
                    className={`m-0 rounded-full border-0 px-2.5 py-0.5 text-xs font-medium ${
                      item.type === "row"
                        ? "bg-[#dcfce7] text-[#166534] dark:bg-[#14532d]/40 dark:text-[#bbf7d0]"
                        : "bg-[#dbeafe] text-[#1d4ed8] dark:bg-[#1e3a8a]/50 dark:text-[#bfdbfe]"
                    }`}
                  >
                    {item.type === "row" ? "行权限" : "列权限"}
                  </Tag>
                ))}
              </Space>
            </Card>
          </Col>
          );
        })}
      </Row>

      <Modal
        title={editing ? "编辑规则组" : "新增规则组"}
        open={open}
        onCancel={() => setOpen(false)}
        footer={
          <Space>
            <Button onClick={() => setOpen(false)}>取消</Button>
            {activeStep === 1 ? <Button onClick={() => setActiveStep(0)}>上一步</Button> : null}
            {activeStep === 0 ? (
              <Button
                type="primary"
                onClick={() => {
                  form
                    .validateFields(["name", "permissions"])
                    .then(() => setActiveStep(1))
                    .catch(() => void 0);
                }}
              >
                下一步
              </Button>
            ) : (
              <Button
                type="primary"
                onClick={() => {
                  form
                    .validateFields()
                    .then(async () => {
                      const values = form.getFieldsValue(true) as RuleForm;
                      const permissionItems = Array.isArray(values.permissions) ? values.permissions : [];
                      if (permissionItems.length === 0) {
                        message.error("请至少添加一条权限规则后再保存");
                        setActiveStep(0);
                        return;
                      }
                      try {
                        await permissionApi.savePermissionGroup({
                          id: values.id,
                          name: values.name,
                          users: values.users || [],
                          permissions: permissionItems.map((item) => {
                            const base = {
                              ...item,
                              expression_tree: item.expression_tree || "{}"
                            };
                            if (item.type !== "column") {
                              return { ...base, permissions: item.permissions || "[]" };
                            }
                            try {
                              const raw = item.permissions || "[]";
                              const rows = typeof raw === "string" ? JSON.parse(raw) : raw;
                              if (!Array.isArray(rows)) {
                                return { ...base, permissions: "[]" };
                              }
                              const cleaned = rows
                                .filter((r: ColumnPermissionRow) => (r.field_name || "").trim().length > 0)
                                .map((r: ColumnPermissionRow) => {
                                  const out: Record<string, unknown> = {
                                    field_name: (r.field_name || "").trim(),
                                    enable: false
                                  };
                                  const c = (r.field_comment || "").trim();
                                  if (c) out.field_comment = c;
                                  if (typeof r.field_id === "number" && r.field_id > 0) {
                                    out.field_id = r.field_id;
                                  }
                                  return out;
                                });
                              return { ...base, permissions: JSON.stringify(cleaned) };
                            } catch {
                              return { ...base, permissions: "[]" };
                            }
                          })
                        });
                        message.success("保存成功");
                        setOpen(false);
                        await reload();
                      } catch (err) {
                        message.error(err instanceof Error ? err.message : "保存失败");
                      }
                    })
                    .catch(() => void 0);
                }}
              >
                保存
              </Button>
            )}
          </Space>
        }
      >
        <Steps
          className="mb-4"
          current={activeStep}
          items={[
            { title: "配置权限规则" },
            { title: "选择受限用户" }
          ]}
        />
        <Form form={form} layout="vertical">
          <Form.Item name="id" hidden>
            <Input />
          </Form.Item>
          {activeStep === 0 ? (
            <>
              <Form.Item name="name" label="规则组名称" rules={[{ required: true, message: "请输入规则组名称" }]}>
                <Input />
              </Form.Item>
              <Divider orientation="left">权限规则项</Divider>
              <Form.List name="permissions">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map((field, idx) => (
                      <Card
                        key={field.key}
                        size="small"
                        className="mb-3"
                        title={`规则项 ${idx + 1}`}
                        extra={
                          fields.length > 1 ? (
                            <Button danger type="text" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
                          ) : null
                        }
                      >
                        <Form.Item
                          name={[field.name, "name"]}
                          label="规则名称"
                          rules={[{ required: true, message: "请输入规则名称" }]}
                        >
                          <Input />
                        </Form.Item>
                        <Form.Item
                          name={[field.name, "type"]}
                          label="权限类型"
                          rules={[{ required: true, message: "请选择权限类型" }]}
                        >
                          <Select
                            options={[
                              { label: "行权限", value: "row" },
                              { label: "列权限", value: "column" }
                            ]}
                          />
                        </Form.Item>
                        <Form.Item name={[field.name, "ds_id"]} label="数据源ID">
                          <Select
                            showSearch
                            options={datasources.map((ds) => ({ label: `${ds.name}(${ds.id})`, value: ds.id }))}
                          />
                        </Form.Item>
                        <Form.Item name={[field.name, "table_name"]} label="数据表名称">
                          <Input placeholder="手动输入数据表名称（例如：users）" />
                        </Form.Item>
                        <Form.Item
                          noStyle
                          shouldUpdate={(prev, next) =>
                            prev.permissions?.[field.name]?.type !== next.permissions?.[field.name]?.type ||
                            prev.permissions?.[field.name]?.table_name !== next.permissions?.[field.name]?.table_name ||
                            prev.permissions?.[field.name]?.expression_tree !==
                              next.permissions?.[field.name]?.expression_tree ||
                            prev.permissions?.[field.name]?.permissions !==
                              next.permissions?.[field.name]?.permissions
                          }
                        >
                          {() => {
                            const typeValue = form.getFieldValue(["permissions", field.name, "type"]);
                            if (typeValue === "row") {
                              const currentExprRaw =
                                form.getFieldValue(["permissions", field.name, "expression_tree"]) || "{}";
                              const parsedExpr = (() => {
                                try {
                                  return JSON.parse(currentExprRaw) as {
                                    relation?: "and" | "or";
                                    conditions?: Array<{ field: string; op: string; value: string }>;
                                  };
                                } catch {
                                  return { relation: "and", conditions: [] };
                                }
                              })();
                              const conditions = parsedExpr.conditions?.length
                                ? parsedExpr.conditions
                                : [{ field: "", op: "=", value: "" }];
                              const relation = parsedExpr.relation || "and";

                              const syncExpression = (nextConditions: Array<{ field: string; op: string; value: string }>, nextRelation = relation) => {
                                form.setFieldValue(
                                  ["permissions", field.name, "expression_tree"],
                                  JSON.stringify({
                                    relation: nextRelation,
                                    conditions: nextConditions
                                  })
                                );
                              };
                              const updateCondition = (
                                index: number,
                                patch: Partial<{ field: string; op: string; value: string }>
                              ) => {
                                const next = [...conditions];
                                next[index] = { ...next[index], ...patch };
                                syncExpression(next);
                              };

                              return (
                                <>
                                  <div className="mb-2 text-xs text-gray-500">行权限条件（SQLBot风格简化版）</div>
                                  <Space className="mb-2" align="center" wrap>
                                    <span className="text-xs text-gray-500">条件关系</span>
                                    <Select
                                      style={{ width: 140 }}
                                      value={relation}
                                      options={[
                                        { label: "AND", value: "and" },
                                        { label: "OR", value: "or" }
                                      ]}
                                      onChange={(value) => syncExpression(conditions, value)}
                                    />
                                  </Space>
                                  {conditions.map((condition, index) => (
                                    <Space key={`${field.name}-condition-${index}`} className="mb-2" wrap>
                                      <Select
                                        placeholder="操作符"
                                        style={{ width: 120 }}
                                        value={condition.op || "="}
                                        options={[
                                          { label: "=", value: "=" },
                                          { label: "!=", value: "!=" },
                                          { label: ">", value: ">" },
                                          { label: "<", value: "<" },
                                          { label: ">=", value: ">=" },
                                          { label: "<=", value: "<=" },
                                          { label: "LIKE", value: "like" }
                                        ]}
                                        onChange={(value) => updateCondition(index, { op: value })}
                                      />
                                      <Input
                                        placeholder="字段（手动输入）"
                                        style={{ width: 180 }}
                                        value={condition.field}
                                        onChange={(e) => updateCondition(index, { field: e.target.value })}
                                      />
                                      <Input
                                        placeholder="值"
                                        style={{ width: 200 }}
                                        value={condition.value}
                                        onChange={(e) => updateCondition(index, { value: e.target.value })}
                                      />
                                      <Button
                                        danger
                                        type="text"
                                        icon={<DeleteOutlined />}
                                        disabled={conditions.length <= 1}
                                        onClick={() => {
                                          const next = conditions.filter((_, i) => i !== index);
                                          syncExpression(next.length ? next : [{ field: "", op: "=", value: "" }]);
                                        }}
                                      />
                                    </Space>
                                  ))}
                                  <Button
                                    type="dashed"
                                    size="small"
                                    icon={<PlusOutlined />}
                                    onClick={() => {
                                      syncExpression([...conditions, { field: "", op: "=", value: "" }]);
                                    }}
                                  >
                                    添加条件
                                  </Button>
                                  <Form.Item name={[field.name, "expression_tree"]} label="表达式JSON（可直接编辑）">
                                    <Input.TextArea rows={2} />
                                  </Form.Item>
                                </>
                              );
                            }
                            const parsedRows = (() => {
                              try {
                                const raw = form.getFieldValue(["permissions", field.name, "permissions"]);
                                return raw ? (JSON.parse(raw) as ColumnPermissionRow[]) : [];
                              } catch {
                                return [];
                              }
                            })();
                            const syncColumnRows = (next: ColumnPermissionRow[]) => {
                              const fixed = next.map((r) => ({ ...r, enable: false }));
                              form.setFieldValue(
                                ["permissions", field.name, "permissions"],
                                JSON.stringify(fixed)
                              );
                            };
                            const patchRow = (index: number, patch: Partial<ColumnPermissionRow>) => {
                              const next = [...parsedRows];
                              const cur = next[index] || {
                                field_name: "",
                                field_comment: "",
                                enable: false
                              };
                              next[index] = { ...cur, ...patch, enable: false };
                              syncColumnRows(next);
                            };
                            return (
                              <>
                                <div className="mb-2 text-xs text-gray-500">
                                  列权限（拒绝列表）：以下字段对受限用户不可见（不出现在对话 schema 与查询结果中）。仅填写需隐藏的列名，与库中列名一致（大小写不敏感）。
                                </div>
                                <div className="mb-2 hidden text-xs font-medium text-gray-500 sm:grid sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:gap-2 sm:px-1">
                                  <span>隐藏字段名</span>
                                  <span>备注</span>
                                  <span className="text-right">操作</span>
                                </div>
                                {parsedRows.length === 0 ? (
                                  <div className="mb-3 rounded-lg border border-dashed border-gray-200 bg-gray-50/80 px-3 py-6 text-center text-sm text-gray-500 dark:border-gray-600 dark:bg-gray-900/40">
                                    暂无受限列，点击下方添加需隐藏的字段
                                  </div>
                                ) : null}
                                {parsedRows.map((row, index) => (
                                  <div
                                    key={`${field.key}-col-${index}`}
                                    className="mb-2 flex flex-col gap-2 rounded-lg border border-gray-100 bg-white/60 p-2 sm:grid sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-center sm:gap-2 dark:border-gray-700 dark:bg-gray-900/30"
                                  >
                                    <Input
                                      placeholder="需隐藏的列名（与表结构一致）"
                                      value={row.field_name}
                                      onChange={(e) => patchRow(index, { field_name: e.target.value })}
                                    />
                                    <Input
                                      placeholder="备注（可选）"
                                      value={row.field_comment ?? ""}
                                      onChange={(e) => patchRow(index, { field_comment: e.target.value })}
                                    />
                                    <div className="flex justify-end">
                                      <Button
                                        danger
                                        type="text"
                                        size="small"
                                        icon={<DeleteOutlined />}
                                        onClick={() => {
                                          syncColumnRows(parsedRows.filter((_, i) => i !== index));
                                        }}
                                      />
                                    </div>
                                  </div>
                                ))}
                                <Button
                                  type="dashed"
                                  size="small"
                                  className="mt-1"
                                  icon={<PlusOutlined />}
                                  onClick={() => {
                                    syncColumnRows([
                                      ...parsedRows,
                                      { field_name: "", field_comment: "", enable: false }
                                    ]);
                                  }}
                                >
                                  添加需隐藏的字段
                                </Button>
                              </>
                            );
                          }}
                        </Form.Item>
                      </Card>
                    ))}
                    <Button
                      type="dashed"
                      block
                      onClick={() =>
                        add({ name: `rule_${fields.length + 1}`, type: "row", expression_tree: "{}", permissions: "[]" })
                      }
                      icon={<PlusOutlined />}
                    >
                      添加规则项
                    </Button>
                  </>
                )}
              </Form.List>
            </>
          ) : (
            <Form.Item name="users" label="限制用户" rules={[{ required: true, message: "请选择受限用户" }]}>
              <Select
                mode="multiple"
                showSearch
                allowClear
                placeholder="选择受限用户"
                onSearch={(value) => {
                  void searchUsers(value);
                }}
                options={candidateUsers.map((u) => ({
                  label: `${u.name} (${u.account})`,
                  value: u.id
                }))}
              />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
}

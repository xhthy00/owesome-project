import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Steps,
  Switch,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import { useEffect, useMemo, useState } from "react";
import { datasourceApi, type DatasourceFieldItem, type DatasourceItem, type DatasourceTableItem } from "@/api/datasource";
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
    table_id?: number;
    expression_tree?: string;
    permissions?: string;
  }>;
};

type ColumnPermissionRow = {
  field_id: number;
  field_name: string;
  field_comment?: string;
  enable: boolean;
};

export default function ConstructPermissionDataRulesPage() {
  const [list, setList] = useState<PermissionGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<PermissionGroup | null>(null);
  const [candidateUsers, setCandidateUsers] = useState<Array<{ id: number; name: string; account: string }>>([]);
  const [activeStep, setActiveStep] = useState(0);
  const [datasources, setDatasources] = useState<DatasourceItem[]>([]);
  const [tableOptions, setTableOptions] = useState<Record<number, DatasourceTableItem[]>>({});
  const [fieldOptions, setFieldOptions] = useState<Record<number, DatasourceFieldItem[]>>({});
  const [form] = Form.useForm<RuleForm>();

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
      setList(res);
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
        {list.map((group) => (
          <Col key={group.id} xs={24} md={12} lg={8}>
            <Card
              title={group.name}
              extra={
                <Space>
                  <Button
                    type="text"
                    onClick={() => {
                      setEditing(group);
                      form.setFieldsValue({
                        id: group.id,
                        name: group.name,
                        users: group.users,
                        permissions: group.permissions.length
                          ? group.permissions.map((p, idx) => ({
                              name: p.name || `rule_${idx + 1}`,
                              type: p.type,
                              ds_id: p.ds_id,
                              table_id: p.table_id,
                              expression_tree: p.expression_tree || "{}",
                              permissions: p.permissions || "[]"
                            }))
                          : [{ name: "rule_1", type: "row", expression_tree: "{}", permissions: "[]" }]
                      });
                      setActiveStep(0);
                      setOpen(true);
                      void searchUsers();
                    }}
                  >
                    设置规则
                  </Button>
                  <Button
                    type="text"
                    onClick={() => {
                      setEditing(group);
                      form.setFieldsValue({
                        id: group.id,
                        name: group.name,
                        users: group.users,
                        permissions: group.permissions.length
                          ? group.permissions.map((p, idx) => ({
                              name: p.name || `rule_${idx + 1}`,
                              type: p.type,
                              ds_id: p.ds_id,
                              table_id: p.table_id,
                              expression_tree: p.expression_tree || "{}",
                              permissions: p.permissions || "[]"
                            }))
                          : [{ name: "rule_1", type: "row", expression_tree: "{}", permissions: "[]" }]
                      });
                      setActiveStep(1);
                      setOpen(true);
                      void searchUsers();
                    }}
                  >
                    设置用户
                  </Button>
                  <Button
                    type="text"
                    icon={<EditOutlined />}
                    onClick={() => {
                      setEditing(group);
                      const first = group.permissions[0];
                      form.setFieldsValue({
                        id: group.id,
                        name: group.name,
                        users: group.users,
                        permissions: group.permissions.length
                          ? group.permissions.map((p, idx) => ({
                              name: p.name || `rule_${idx + 1}`,
                              type: p.type,
                              ds_id: p.ds_id,
                              table_id: p.table_id,
                              expression_tree: p.expression_tree || "{}",
                              permissions: p.permissions || "[]"
                            }))
                          : [{ name: "rule_1", type: "row", expression_tree: "{}", permissions: "[]" }]
                      });
                      setActiveStep(0);
                      setOpen(true);
                      void searchUsers();
                    }}
                  />
                  <Popconfirm
                    title={`删除规则组 ${group.name} ?`}
                    description="删除后该组全部规则立即失效，相关成员恢复对应数据访问权限。"
                    onConfirm={async () => {
                      await permissionApi.deletePermissionGroup(group.id);
                      message.success("删除成功");
                      await reload();
                    }}
                  >
                    <Button type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              }
            >
              <div className="mb-2 text-sm text-gray-500">限制用户数: {group.users.length}</div>
              <div className="mb-2 text-sm text-gray-500">规则条数: {group.permissions.length}</div>
              <Space wrap>
                {group.permissions.map((item) => (
                  <Tag key={`${group.id}-${item.id || item.name}`} color={item.type === "row" ? "green" : "blue"}>
                    {item.type === "row" ? "行权限" : "列权限"}
                  </Tag>
                ))}
              </Space>
            </Card>
          </Col>
        ))}
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
                    .then(async (values) => {
                      await permissionApi.savePermissionGroup({
                        id: values.id,
                        name: values.name,
                        users: values.users || [],
                        permissions: (values.permissions || []).map((item) => ({
                          ...item,
                          expression_tree: item.expression_tree || "{}",
                          permissions: item.permissions || "[]"
                        }))
                      });
                      message.success("保存成功");
                      setOpen(false);
                      await reload();
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
                            onChange={(value) => {
                              void datasourceApi.tableList(value).then((rows) => {
                                setTableOptions((prev) => ({ ...prev, [field.name]: rows }));
                                setFieldOptions((prev) => ({ ...prev, [field.name]: [] }));
                              });
                            }}
                          />
                        </Form.Item>
                        <Form.Item name={[field.name, "table_id"]} label="数据表ID">
                          <Input placeholder="手动输入数据表ID（例如：12）" />
                        </Form.Item>
                        <Form.Item
                          noStyle
                          shouldUpdate={(prev, next) =>
                            prev.permissions?.[field.name]?.type !== next.permissions?.[field.name]?.type ||
                            prev.permissions?.[field.name]?.table_id !== next.permissions?.[field.name]?.table_id ||
                            prev.permissions?.[field.name]?.expression_tree !==
                              next.permissions?.[field.name]?.expression_tree
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
                            return (
                              <>
                                <div className="mb-2 text-xs text-gray-500">列权限（与 SQLBot 一样按字段开关）</div>
                                <Table
                                  size="small"
                                  rowKey="field_id"
                                  columns={[
                                    { title: "字段名", dataIndex: "field_name" },
                                    { title: "备注", dataIndex: "field_comment" },
                                    {
                                      title: "可见",
                                      width: 120,
                                      render: (_, row: ColumnPermissionRow, index) => (
                                        <Switch
                                          checked={row.enable}
                                          onChange={(checked) => {
                                            const cloned = [...parsedRows];
                                            cloned[index] = { ...cloned[index], enable: checked };
                                            form.setFieldValue(
                                              ["permissions", field.name, "permissions"],
                                              JSON.stringify(cloned)
                                            );
                                          }}
                                        />
                                      )
                                    }
                                  ]}
                                  dataSource={parsedRows}
                                  pagination={false}
                                />
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

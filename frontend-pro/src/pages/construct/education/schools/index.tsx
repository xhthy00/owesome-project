import { BankOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, Form, Input, Modal, Select, Space, Table, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { getCurrentUser } from "@/api/auth";
import {
  schoolApi,
  type SchoolImpact,
  type SchoolItem,
  type SchoolMeta,
  type SchoolPayload
} from "@/api/school";

const TABLE_CLASS =
  "[&_.ant-table]:!text-[13px] [&_.ant-table-thead>tr>th]:!bg-[#f8fafc] [&_.ant-table-thead>tr>th]:!font-semibold [&_.ant-table-thead>tr>th]:!text-[#334155] dark:[&_.ant-table-thead>tr>th]:!bg-[#1b2433] dark:[&_.ant-table-thead>tr>th]:!text-[#cbd5e1]";

const EMPTY_FORM: SchoolPayload = {
  s_name: "",
  district: "",
  stage: "高中",
  type: ""
};

const TYPE_TONE: Record<string, string> = {
  引领校: "bg-[#e8f3ff] text-[#0C75FC] ring-[#c5ddff]",
  支撑校: "bg-[#e7f7f4] text-[#0f766e] ring-[#b7e6df]",
  发展校: "bg-[#fff6e8] text-[#b86e00] ring-[#ffe1b3]",
  其他校: "bg-[#f4f6f8] text-[#52617d] ring-[#e2e8f0]",
  市报: "bg-[#fff1f2] text-[#be123c] ring-[#fecdd3]"
};

const FALLBACK_TYPES = ["引领校", "支撑校", "发展校", "其他校", "市报"];

function typeTone(value: string): string {
  return TYPE_TONE[value] || TYPE_TONE["其他校"];
}

function TypeMark({ value }: { value: string }) {
  if (!value) return <span className="text-[#cbd5e1]">-</span>;
  return (
    <span className={`inline-flex rounded-md px-2 py-0.5 text-[12px] font-medium ring-1 ${typeTone(value)}`}>
      {value}
    </span>
  );
}

function ImpactBody({ impact }: { impact: SchoolImpact }) {
  const linked = impact.dependencies.filter((item) => item.count > 0);
  return (
    <div className="mt-2 space-y-3 text-[13px] leading-6 text-[#334155]">
      {impact.notes.map((note) => (
        <p key={note} className="!mb-0">
          {note}
        </p>
      ))}
      {impact.changes.length ? (
        <div>
          <div className="mb-1 font-medium text-[#1e293b]">本次变更</div>
          <ul className="!mb-0 list-disc pl-5">
            {impact.changes.map((item) => (
              <li key={item.field}>
                {item.label}：{item.before || "空"} → {item.after || "空"}。{item.effect}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div>
        <div className="mb-1 font-medium text-[#1e293b]">关联数据</div>
        {linked.length ? (
          <ul className="!mb-0 list-disc pl-5">
            {linked.map((item) => (
              <li key={item.key}>
                {item.label} {item.count} {item.unit}
              </li>
            ))}
          </ul>
        ) : (
          <p className="!mb-0">未发现成绩、学生、试卷、考试、指标、尖子班、宽表或账号依赖。</p>
        )}
      </div>
    </div>
  );
}

export default function ConstructSchoolsPage() {
  const [keyword, setKeyword] = useState("");
  const [district, setDistrict] = useState("");
  const [schoolType, setSchoolType] = useState("");
  const [applied, setApplied] = useState({ keyword: "", district: "", schoolType: "" });
  const [districts, setDistricts] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [typeCounts, setTypeCounts] = useState<Array<{ type: string; count: number }>>([]);
  const [catalogTotal, setCatalogTotal] = useState(0);
  const [stages, setStages] = useState<string[]>(["高中", "初中", "小学"]);
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<SchoolItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<SchoolItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [form] = Form.useForm<SchoolPayload>();

  const loadList = useCallback(
    async (
      query: { keyword: string; district: string; schoolType: string },
      nextPage: number,
      nextSize: number
    ) => {
      setLoading(true);
      try {
        const data = await schoolApi.list({
          page: nextPage,
          pageSize: nextSize,
          keyword: query.keyword,
          district: query.district,
          schoolType: query.schoolType
        });
        setItems(data.items || []);
        setTotal(data.total || 0);
        setPage(data.page || nextPage);
        setPageSize(data.page_size || nextSize);
      } catch (err) {
        message.error(err instanceof Error ? err.message : "加载学校失败");
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    let active = true;
    void getCurrentUser()
      .then((user) => {
        if (!active) return;
        const ok = Boolean(user.is_platform_admin || user.is_super_admin);
        setAllowed(ok);
        if (!ok) return;
        void schoolApi.meta().then((meta) => {
          if (!active) return;
          applyMeta(meta);
        });
        void loadList({ keyword: "", district: "", schoolType: "" }, 1, 20);
      })
      .catch(() => {
        if (active) setAllowed(false);
      });
    return () => {
      active = false;
    };
  }, [loadList]);

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue(EMPTY_FORM);
    setFormOpen(true);
  };

  const openEdit = (row: SchoolItem) => {
    setEditing(row);
    form.setFieldsValue({
      s_name: row.s_name,
      district: row.district,
      stage: row.stage || "高中",
      type: row.type
    });
    setFormOpen(true);
  };

  const applyMeta = (meta: SchoolMeta) => {
    setDistricts(meta.districts || []);
    setTypes(meta.types || []);
    if (meta.stages?.length) setStages(meta.stages);
    setTypeCounts(meta.type_counts || []);
    setCatalogTotal(meta.total || 0);
  };

  const refreshAfterWrite = (nextPage = page) => {
    void schoolApi.meta().then(applyMeta).catch(() => undefined);
    void loadList(applied, nextPage, pageSize);
  };

  const search = (next: { keyword: string; district: string; schoolType: string }) => {
    setApplied(next);
    void loadList(next, 1, pageSize);
  };

  const saveSchool = async (values: SchoolPayload) => {
    setSaving(true);
    try {
      if (editing) {
        await schoolApi.update(editing.id, values);
      } else {
        await schoolApi.create(values);
      }
      message.success(editing ? "保存成功" : "新增成功");
      setFormOpen(false);
      refreshAfterWrite(editing ? page : 1);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const submit = async () => {
    const values = await form.validateFields();
    if (!editing) {
      await saveSchool(values);
      return;
    }
    const unchanged =
      values.s_name.trim() === editing.s_name &&
      values.district.trim() === editing.district &&
      values.stage.trim() === (editing.stage || "") &&
      values.type.trim() === editing.type;
    if (unchanged) {
      message.info("没有修改");
      return;
    }
    setSaving(true);
    let impact: SchoolImpact;
    try {
      impact = await schoolApi.preview(editing.id, values);
    } catch (err) {
      setSaving(false);
      message.error(err instanceof Error ? err.message : "评估学校影响失败");
      return;
    }
    setSaving(false);
    const nameChanged = values.s_name.trim() !== editing.s_name;
    const blocked = nameChanged && !impact.can_rename;
    Modal.confirm({
      title: blocked ? "不能修改学校名称" : "确认修改学校",
      width: 560,
      okText: blocked ? "知道了" : "确认保存",
      cancelText: "取消",
      okButtonProps: blocked ? { type: "default" } : undefined,
      cancelButtonProps: blocked ? { style: { display: "none" } } : undefined,
      content: (
        <div>
          {blocked ? (
            <p className="!mb-0 text-[13px] text-[#334155]">学校名称不能修改，本次不会保存。</p>
          ) : null}
          <ImpactBody impact={impact} />
        </div>
      ),
      onOk: async () => {
        if (blocked) return;
        await saveSchool(values);
      }
    });
  };

  const remove = async (row: SchoolItem) => {
    let impact: SchoolImpact;
    try {
      impact = await schoolApi.impact(row.id);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "评估学校影响失败");
      return;
    }
    if (!impact.can_delete) {
      Modal.warning({
        title: "无法删除学校",
        width: 560,
        content: <ImpactBody impact={impact} />
      });
      return;
    }
    Modal.confirm({
      title: "删除学校",
      width: 560,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      content: <ImpactBody impact={impact} />,
      onOk: async () => {
        try {
          await schoolApi.remove(row.id);
        } catch (err) {
          message.error(err instanceof Error ? err.message : "删除失败");
          throw err;
        }
        message.success("删除成功");
        refreshAfterWrite(page);
      }
    });
  };

  const tierOptions = typeCounts.length
    ? typeCounts
    : (types.length ? types : FALLBACK_TYPES).map((type) => ({ type, count: 0 }));

  const columns: ColumnsType<SchoolItem> = [
    {
      title: "学校编码",
      dataIndex: "id",
      width: "16%",
      ellipsis: true,
      render: (value: string) => (
        <span className="font-mono text-[13px] tracking-wide text-[#3d4a64] dark:text-slate-300">{value}</span>
      )
    },
    {
      title: "学校名称",
      dataIndex: "s_name",
      width: "24%",
      ellipsis: true,
      render: (name: string) => (
        <span className="truncate text-[14px] font-medium text-[#1c2533] dark:text-slate-100">{name}</span>
      )
    },
    {
      title: "区县",
      dataIndex: "district",
      width: "15%",
      align: "center",
      render: (value: string) =>
        value ? (
          <span className="inline-flex rounded-md bg-[#f4f7fb] px-2 py-0.5 text-[12px] text-[#3d4a64] ring-1 ring-[#e3e9f2] dark:bg-[#1b2433] dark:text-slate-300 dark:ring-[#334155]">
            {value}
          </span>
        ) : (
          <span className="text-[#cbd5e1]">-</span>
        )
    },
    {
      title: "学段",
      dataIndex: "stage",
      width: "13%",
      align: "center",
      render: (value: string) => value || "-"
    },
    {
      title: "层级",
      dataIndex: "type",
      width: "16%",
      align: "center",
      render: (value: string) => <TypeMark value={value} />
    },
    {
      title: "操作",
      key: "actions",
      width: "16%",
      align: "center",
      render: (_, row) => (
        <div className="inline-flex items-center justify-center gap-1">
          <Button type="link" size="small" className="!px-1" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <span className="text-[#e5e7eb]">|</span>
          <Button type="link" size="small" danger className="!px-1" onClick={() => void remove(row)}>
            删除
          </Button>
        </div>
      )
    }
  ];

  if (allowed !== true) {
    if (allowed === false) {
      return (
        <div className="dbgpt-ui-font p-6">
          <Typography.Text type="secondary">仅系统管理员可维护学校</Typography.Text>
        </div>
      );
    }
    return <div className="dbgpt-ui-font h-full" />;
  }

  return (
    <div className="dbgpt-ui-font h-full min-h-0 overflow-y-auto bg-[#f3f6fb] p-6 pb-10 dark:bg-transparent">
      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm dark:border-[#334155] dark:bg-[#141923]">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#e8eef6] bg-[linear-gradient(180deg,#eef5ff_0%,#ffffff_78%)] px-5 py-4 dark:border-[#334155] dark:bg-none">
          <div className="flex min-w-0 items-center gap-3">
            <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#0C75FC] text-[18px] text-white shadow-[0_6px_16px_rgba(12,117,252,0.28)]">
              <BankOutlined />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-2">
                <Typography.Title level={4} className="!mb-0">
                  学校
                </Typography.Title>
                <span className="text-[13px] text-[#64748b]">
                  {catalogTotal} 所 · {districts.length} 个区县
                </span>
              </div>
              <Typography.Text type="secondary" className="text-[13px]">
                有成绩、试卷或账号关联时，不能改名称，也不能删除。区县、学段和层级可以改。
              </Typography.Text>
            </div>
          </div>
          <Space wrap>
            <Button icon={<ReloadOutlined />} onClick={() => void loadList(applied, page, pageSize)} loading={loading}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新增学校
            </Button>
          </Space>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-b border-[#eef2f6] px-5 py-3 dark:border-[#334155]">
          <button
            type="button"
            onClick={() => {
              setSchoolType("");
              search({ keyword: keyword.trim(), district, schoolType: "" });
            }}
            className={`rounded-full px-3 py-1 text-[13px] ring-1 transition ${
              schoolType
                ? "bg-white text-[#3d4a64] ring-[#e5e7eb] hover:ring-[#93c5fd] dark:bg-transparent dark:text-slate-300 dark:ring-[#334155]"
                : "bg-[#0C75FC] text-white ring-[#0C75FC]"
            }`}
          >
            全部 {catalogTotal}
          </button>
          {tierOptions.map((item) => {
            const active = schoolType === item.type;
            return (
              <button
                key={item.type}
                type="button"
                onClick={() => {
                  const nextType = active ? "" : item.type;
                  setSchoolType(nextType);
                  search({ keyword: keyword.trim(), district, schoolType: nextType });
                }}
                className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] ring-1 transition ${
                  active
                    ? "bg-[#0C75FC] text-white ring-[#0C75FC]"
                    : `bg-white hover:brightness-[0.98] dark:bg-transparent ${typeTone(item.type)}`
                }`}
              >
                {item.type}
                <span className={active ? "text-white/80" : "opacity-70"}>{item.count}</span>
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-end gap-3 bg-[#f8fafc] px-5 py-3 dark:bg-[#101722]">
          <label className="flex w-[240px] flex-col gap-1 text-xs text-[#64748b]">
            <span>学校</span>
            <Input
              allowClear
              placeholder="名称、区县或编码"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onPressEnter={() => search({ keyword: keyword.trim(), district, schoolType })}
            />
          </label>
          <label className="flex w-[148px] flex-col gap-1 text-xs text-[#64748b]">
            <span>区县</span>
            <Select
              allowClear
              showSearch
              placeholder="全部区县"
              value={district || undefined}
              options={districts.map((item) => ({ label: item, value: item }))}
              onChange={(value) => setDistrict(value || "")}
            />
          </label>
          <div className="flex items-center gap-2 pb-0.5">
            <Button
              type="primary"
              icon={<SearchOutlined />}
              onClick={() => search({ keyword: keyword.trim(), district, schoolType })}
            >
              查询
            </Button>
            <Button
              onClick={() => {
                setKeyword("");
                setDistrict("");
                setSchoolType("");
                search({ keyword: "", district: "", schoolType: "" });
              }}
            >
              重置
            </Button>
          </div>
        </div>

        <Table
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={items}
          tableLayout="fixed"
          size="middle"
          className={TABLE_CLASS}
          locale={{ emptyText: "暂无学校" }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            showTotal: (count) => `共 ${count} 条`,
            className: "!px-5 !py-3",
            onChange: (nextPage, nextSize) => {
              void loadList(applied, nextPage, nextSize);
            }
          }}
        />
      </div>

      <Modal
        title={editing ? "编辑学校" : "新增学校"}
        open={formOpen}
        onCancel={() => setFormOpen(false)}
        onOk={() => void submit()}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={560}
        forceRender
      >
        <Form form={form} layout="vertical" initialValues={EMPTY_FORM} className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          {editing ? (
            <Form.Item label="学校编码" className="sm:col-span-2">
              <Input value={editing.id} disabled />
            </Form.Item>
          ) : null}
          <Form.Item
            name="s_name"
            label="学校名称"
            className="sm:col-span-2"
            rules={[{ required: true, message: "请填写学校名称" }]}
          >
            <Input maxLength={64} placeholder="与成绩宽表中的学校名一致" />
          </Form.Item>
          <Form.Item name="district" label="区县" rules={[{ required: true, message: "请填写区县" }]}>
            <Input maxLength={64} placeholder="例如 广陵区" />
          </Form.Item>
          <Form.Item name="stage" label="学段" rules={[{ required: true, message: "请选择学段" }]}>
            <Select options={stages.map((item) => ({ label: item, value: item }))} />
          </Form.Item>
          <Form.Item
            name="type"
            label="层级"
            className="sm:col-span-2"
            rules={[{ required: true, message: "请选择层级" }]}
          >
            <Select
              options={(types.length ? types : FALLBACK_TYPES).map((item) => ({
                label: item,
                value: item
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

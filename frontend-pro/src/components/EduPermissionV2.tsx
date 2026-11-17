import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Drawer, Form, Input, Modal, Radio, Select, Space, Table, Tag, Typography, message } from "antd";
import { permissionApi, type EduAuthorizationV2, type EduGrantV2, type EduPermissionUser, type EduScopeLevel, type EduSourceInfo } from "@/api/permission";

const levels: Record<EduScopeLevel, string> = {
  city: "市级", district: "区县级", school: "学校级", grade: "年级", class: "班级", student: "学生"
};
const required: Record<EduScopeLevel, string[]> = {
  city: ["city_id"], district: ["city_id", "district_id"], school: ["city_id", "school_id"],
  grade: ["city_id", "school_id", "cohort_id"], class: ["city_id", "school_id", "cohort_id", "class_id"],
  student: ["city_id", "school_id", "cohort_id", "student_id"]
};
const fieldLabels: Record<string, string> = {
  city_id: "城市", district_id: "区县", school_id: "学校", cohort_id: "届次", class_id: "班级", student_id: "学生匿名标识"
};
const capabilityLabels: Record<string, string> = {
  grade_baseline: "本校同届次年级整体基准", city_baseline: "全市整体基准", district_self_rank: "本区全市名次（仅自身）"
};
const scopeLabel = (grant: EduGrantV2) => [levels[grant.level], grant.district_id, grant.school_id,
  grant.cohort_id, grant.class_id, grant.student_id].filter(Boolean).join(" / ");
// Compare meaning, independent of ID, property order and omitted nullable/default fields.
const signature = (g: EduGrantV2) => JSON.stringify([g.workspace_oid, g.datasource_id, g.level,
  ...Object.keys(fieldLabels).map(k => (g as unknown as Record<string, unknown>)[k] || null),
  [...g.capabilities].sort(), g.enabled, !!g.can_delegate, g.valid_from || null, g.valid_to || null,
  g.history_from || null, g.history_to || null, g.remark || ""]);
const csvCell = (value: string | number) => `"${String(value).replaceAll('"', '""')}"`;
type ReviewRow = { id: number; account: string; name: string; before: EduGrantV2[]; after: EduGrantV2[]; changed: boolean };
type BulkReview = { csv: string; rows: ReviewRow[]; operation: string; changed: number };
const grantDetails = (grants: EduGrantV2[]) => grants.length ? <Space direction="vertical" size="small">{grants.map(g =>
  <div key={g.id}>
    <div>{scopeLabel(g)}</div>
    <Typography.Text type="secondary">{g.enabled ? "启用" : "停用"} · {g.can_delegate ? "可委派" : "不可委派"}
      {g.capabilities.length ? ` · ${g.capabilities.map(c => capabilityLabels[c] || c).join("、")}` : " · 无对照能力"}
      {g.valid_from ? ` · 从 ${g.valid_from}` : ""}{g.valid_to ? ` · 至 ${g.valid_to}` : " · 长期"}
      {g.history_from || g.history_to ? ` · 考试日期 ${g.history_from || "不限"} 至 ${g.history_to || "不限"}` : ""}
      {g.remark ? ` · ${g.remark}` : ""}
    </Typography.Text>
  </div>)}</Space> : "无";

export default function EduPermissionV2() {
  const [source, setSource] = useState<EduSourceInfo>();
  const datasourceId = source?.datasource_id;
  const [catalog, setCatalog] = useState<Array<Record<string, string>>>([]);
  const [users, setUsers] = useState<EduPermissionUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [selected, setSelected] = useState<EduPermissionUser[]>([]);
  const [activeUser, setActiveUser] = useState<EduPermissionUser>();
  const [document, setDocument] = useState<EduAuthorizationV2>();
  const [loading, setLoading] = useState(false);
  const [usersLoading, setUsersLoading] = useState(false);
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formMode, setFormMode] = useState<"single" | "bulk">("single");
  const [bulkMode, setBulkMode] = useState<"append" | "replace">("append");
  const [review, setReview] = useState<BulkReview>();
  const [form] = Form.useForm<EduGrantV2>();
  const formValues = Form.useWatch([], form) as Partial<EduGrantV2> | undefined;
  const level = formValues?.level || "class";
  const [sql, setSql] = useState("");
  const [preview, setPreview] = useState("");
  const [csv, setCsv] = useState("");
  const [validatedCsv, setValidatedCsv] = useState("");
  const sequence = useRef(0);
  const userRequest = useRef(0);

  const load = async (refresh = false) => {
    const request = ++sequence.current;
    setError(""); setValidatedCsv(""); setSelected([]); setReview(undefined);
    setLoading(true);
    try {
      const fixedSource = await permissionApi.eduV2Source();
      const directory = await permissionApi.eduV2Catalog(undefined, refresh);
      if (request !== sequence.current) return;
      setSource(fixedSource); setCatalog(directory.items); setRefreshKey(k => k + 1);
    } catch (err) {
      if (request === sequence.current) { setSource(undefined); setError(err instanceof Error ? err.message : "加载权限失败"); }
    } finally { if (request === sequence.current) setLoading(false); }
  };
  useEffect(() => { void load(); return () => { sequence.current += 1; userRequest.current += 1; }; }, []);
  useEffect(() => {
    if (!datasourceId) return;
    let cancelled = false;
    setUsersLoading(true); setListError(""); setUsers([]);
    void permissionApi.eduV2Users(page, pageSize, keyword).then(result => {
      if (!cancelled) { setUsers(result.items); setTotal(result.total); }
    }).catch(err => {
      if (!cancelled) setListError(err instanceof Error ? err.message : "加载账号列表失败");
    }).finally(() => { if (!cancelled) setUsersLoading(false); });
    return () => { cancelled = true; };
  }, [datasourceId, page, pageSize, keyword, refreshKey]);

  const openUser = async (user: EduPermissionUser) => {
    const request = ++userRequest.current;
    setActiveUser(user); setDocument(undefined); setPreview(""); setSql(""); setLoading(true);
    try {
      const doc = await permissionApi.getEduV2(user.id, datasourceId!);
      if (request === userRequest.current) setDocument(doc);
    } catch (err) { message.error(err instanceof Error ? err.message : "加载用户授权失败"); }
    finally { if (request === userRequest.current) setLoading(false); }
  };
  const persist = async (grants: EduGrantV2[]) => {
    if (!activeUser || !datasourceId || !document) return;
    setLoading(true);
    try {
      const saved = await permissionApi.saveEduV2(activeUser.id, datasourceId, document.revision, grants);
      setDocument(saved); setPreview(""); setFormOpen(false); setSelected([]); setRefreshKey(k => k + 1);
      message.success("分级权限已保存");
    } catch (err) { message.error(err instanceof Error ? err.message : "保存失败，请刷新后重试"); }
    finally { setLoading(false); }
  };
  const openGrant = (grant?: EduGrantV2, bulk = false) => {
    setFormMode(bulk ? "bulk" : "single"); setBulkMode("append");
    setEditing(grant?.id || null); form.resetFields();
    form.setFieldsValue(grant || { level: "class", city_id: source?.city_id, enabled: true, capabilities: [], can_delegate: false });
    setFormOpen(true);
  };
  const prepareBulk = async (grant?: EduGrantV2) => {
    if (!datasourceId || !selected.length) return;
    const rows: ReviewRow[] = selected.map(user => {
      const before = user.document!.grants;
      const equivalent = grant && before.find(g => signature(g) === signature(grant));
      const after = !grant ? [] : bulkMode === "replace" ? [equivalent || { ...grant, id: `grant-${crypto.randomUUID()}` }]
        : equivalent ? before : [...before, { ...grant, id: `grant-${crypto.randomUUID()}` }];
      const changed = (!grant && !user.managed) || before.length !== after.length || before.some((g, index) => signature(g) !== signature(after[index]));
      return { id: user.id, account: user.account, name: user.name, before, after, changed };
    });
    const changedRows = rows.filter(row => row.changed);
    if (!changedRows.length) { message.info("所选账号已具有相同配置，无需重复授权"); return; }
    const revisions = new Map(selected.map(u => [u.id, u.document!.revision]));
    const batchCsv = ["user_id,expected_revision,grants_json", ...changedRows.map(row =>
      [row.id, revisions.get(row.id)!, JSON.stringify(row.after)].map(csvCell).join(","))].join("\n");
    if (batchCsv.length > 1_000_000) { message.warning("本批次授权内容较多，请减少选择的账号后重试"); return; }
    setLoading(true);
    try {
      await permissionApi.batchEduV2(datasourceId, batchCsv, false);
      setReview({ csv: batchCsv, rows, operation: !grant ? "撤销全部范围" : bulkMode === "replace" ? "替换全部范围" : "追加范围", changed: changedRows.length });
      setFormOpen(false);
    } catch (err) { message.error(err instanceof Error ? err.message : "批量校验失败，请刷新账号列表后重试"); }
    finally { setLoading(false); }
  };
  const saveGrant = async () => {
    let values: EduGrantV2;
    try { values = await form.validateFields(); } catch { return; }
    if (!source) return;
    const grant: EduGrantV2 = {
      id: editing || `grant-${crypto.randomUUID()}`, workspace_oid: source.workspace_oid, datasource_id: source.datasource_id,
      level: values.level, city_id: source.city_id, enabled: values.enabled,
      capabilities: values.capabilities || [], can_delegate: values.can_delegate || false, remark: values.remark || "",
      valid_from: values.valid_from ? new Date(values.valid_from).toISOString() : null,
      valid_to: values.valid_to ? new Date(values.valid_to).toISOString() : null,
      history_from: values.history_from || null, history_to: values.history_to || null
    };
    for (const key of required[values.level]) {
      (grant as unknown as Record<string, unknown>)[key] = (values as unknown as Record<string, unknown>)[key];
    }
    if (formMode === "bulk") await prepareBulk(grant);
    else if (document) await persist([...document.grants.filter(g => g.id !== editing), grant]);
  };
  const applyReview = async () => {
    if (!review || !datasourceId) return;
    setLoading(true);
    try {
      const result = await permissionApi.batchEduV2(datasourceId, review.csv, true);
      message.success(`已更新 ${result.applied} 位用户`);
      setReview(undefined); setSelected([]); setRefreshKey(k => k + 1);
    } catch (err) {
      setReview(undefined);
      message.error(err instanceof Error ? err.message : "应用失败，未写入本批次，请刷新后重新校验");
    } finally { setLoading(false); }
  };
  const previewEffective = async () => {
    if (!activeUser || !datasourceId) return;
    setLoading(true);
    try { setPreview(JSON.stringify(await permissionApi.previewEduV2(activeUser.id, datasourceId, sql), null, 2)); }
    catch (err) { message.error(err instanceof Error ? err.message : "预览失败"); }
    finally { setLoading(false); }
  };
  const batch = async (apply: boolean) => {
    if (!datasourceId) return;
    setLoading(true);
    try {
      const result = await permissionApi.batchEduV2(datasourceId, csv, apply);
      setValidatedCsv(apply ? "" : `${datasourceId}:${csv}`);
      message.success(apply ? `已更新 ${result.applied} 位用户` : `校验通过，共 ${result.validated} 位用户`);
      if (apply) { setSelected([]); setRefreshKey(k => k + 1); }
    } catch (err) { setValidatedCsv(""); message.error(err instanceof Error ? err.message : "批量操作失败"); }
    finally { setLoading(false); }
  };
  const options = (key: string) => {
    const preceding = required[level].slice(0, required[level].indexOf(key));
    const rows = catalog.filter(row => preceding.every(k => !(formValues as Record<string, unknown>)?.[k] || row[k] === (formValues as Record<string, unknown>)[k]));
    return [...new Set(rows.map(row => row[key]).filter(Boolean))].map(value => ({ value, label: value }));
  };

  return <Card title="分级数据权限" className="!mb-6" extra={<Button loading={loading} onClick={() => void load(true)}>刷新</Button>}>
    <Typography.Paragraph type="secondary">搜索并勾选账号，统一配置相同学校、年级或班级的权限。支持跨页多选，一次最多 500 个账号；各自任教班级不同的老师可单独管理。</Typography.Paragraph>
    {(error || listError) && <Alert type="error" showIcon message={error || listError} className="!mb-3" />}
    <Space wrap className="!mb-4">
      <Tag color="blue">学情业务库</Tag>
      <Input.Search aria-label="搜索权限账号" placeholder="搜索姓名或账号" allowClear enterButton="搜索" style={{ width: 300, maxWidth: "100%" }}
        onSearch={value => { setKeyword(value); setPage(1); }} />
      <Button type="primary" disabled={!selected.length || loading || !!error} onClick={() => openGrant(undefined, true)}>批量授权</Button>
      <Button danger disabled={!selected.length || loading || !!error} onClick={() => void prepareBulk()}>批量撤销</Button>
    </Space>
    <Space wrap className="!mb-3" aria-live="polite">
      <Typography.Text>共 {total} 个账号，已选择 {selected.length} 个（跨页保留）</Typography.Text>
      {selected.length > 0 && <Button type="link" onClick={() => setSelected([])}>清空选择</Button>}
    </Space>
    <Table<EduPermissionUser> rowKey="id" size="small" loading={usersLoading} dataSource={users} scroll={{ x: 850 }}
      rowSelection={{ selectedRowKeys: selected.map(u => u.id), preserveSelectedRowKeys: true,
        getCheckboxProps: user => ({ disabled: !user.manageable || !user.document || loading, "aria-label": `选择账号 ${user.account}` }),
        onChange: (_, rows) => { if (rows.length > 500) message.warning("一次最多选择 500 个账号"); else setSelected(rows); } }}
      pagination={{ current: page, pageSize, total, showSizeChanger: true, pageSizeOptions: [20, 50, 100],
        onChange: (next, size) => { setPage(size !== pageSize ? 1 : next); setPageSize(size); } }}
      columns={[
        { title: "姓名 / 账号", render: (_, user) => <><Typography.Text strong>{user.name}</Typography.Text><div>{user.account}</div></> },
        { title: "账号状态", render: (_, user) => user.status === 1 ? "启用" : "停用" },
        { title: "授权状态", render: (_, user) => !user.manageable ? <Tag>不可管理</Tag> : !user.managed ? <Tag>未配置</Tag>
          : user.active_count ? <Tag color="green">{user.active_count} 个有效范围</Tag> : <Tag color="orange">无有效范围</Tag> },
        { title: "已配置范围", render: (_, user) => user.message || (user.document?.grants.length
          ? <Space direction="vertical" size={0}>{user.document.grants.slice(0, 3).map(g => <span key={g.id}>{scopeLabel(g)}</span>)}
            {user.document.grants.length > 3 && <Typography.Text type="secondary">另有 {user.document.grants.length - 3} 个范围</Typography.Text>}</Space> : "—") },
        { title: "操作", render: (_, user) => <Button disabled={!user.manageable || loading} onClick={() => void openUser(user)} aria-label={`管理权限 ${user.account}`}>管理权限</Button> }
      ]} />
    <details className="mt-4"><summary>高级：差异化 CSV 导入</summary>
      <Typography.Paragraph type="secondary" className="!mt-2">用于每位用户的班级不同等情况。CSV 表头为 user_id,expected_revision,grants_json。先校验，再应用；任意用户校验失败时整个批次不写入。</Typography.Paragraph>
      <Input.TextArea aria-label="分级权限CSV" rows={4} value={csv} onChange={e => { setCsv(e.target.value); setValidatedCsv(""); }} />
      <Space className="!mt-2"><Button disabled={!datasourceId || !csv || loading} onClick={() => void batch(false)}>校验导入</Button>
        <Button disabled={!datasourceId || validatedCsv !== `${datasourceId}:${csv}` || loading} onClick={() => void batch(true)}>应用已校验批次</Button></Space>
    </details>
    <Drawer title={`${activeUser?.name || ""}（${activeUser?.account || ""}）的权限`} open={!!activeUser} width="min(960px, 100vw)"
      onClose={() => { userRequest.current += 1; setActiveUser(undefined); setDocument(undefined); setLoading(false); }}>
      <Space className="!mb-4"><Button disabled={!document || loading} type="primary" onClick={() => openGrant()}>添加范围</Button>
        <Button disabled={loading} onClick={() => activeUser && void openUser(activeUser)}>重新加载</Button></Space>
      <Table<EduGrantV2> rowKey="id" size="small" loading={loading} dataSource={document?.grants || []} pagination={false} scroll={{ x: 700 }}
        columns={[
          { title: "范围", render: (_, grant) => scopeLabel(grant) },
          { title: "允许对照", render: (_, grant) => grant.capabilities.map(c => <Tag key={c}>{capabilityLabels[c]}</Tag>) },
          { title: "有效性", render: (_, grant) => <span>{grant.enabled ? "启用" : "停用"}{grant.valid_to ? ` · 至 ${grant.valid_to}` : ""}</span> },
          { title: "操作", render: (_, grant) => <Space><Button size="small" disabled={loading} onClick={() => openGrant(grant)}>编辑</Button>
            <Button size="small" danger disabled={loading} onClick={() => Modal.confirm({ title: "撤销此范围？", content: "撤销后将按剩余授权查询，旧结果需要重新验证。", onOk: () => persist(document!.grants.filter(g => g.id !== grant.id)) })}>撤销</Button></Space> }
        ]} />
      <details className="mt-4"><summary>检查生效范围与查询权限</summary>
        <Input.TextArea aria-label="权限校验语义SQL" className="!my-3" rows={4} value={sql} onChange={e => setSql(e.target.value)} placeholder="可选：粘贴模型生成的语义 SQL，检查其权限条件" />
        <Button disabled={!document || loading} onClick={() => void previewEffective()}>检查权限</Button>
        {preview && <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{preview}</pre>}
      </details>
    </Drawer>
    <Modal title={formMode === "bulk" ? `为 ${selected.length} 个账号批量授权` : editing ? "编辑授权范围" : "添加授权范围"}
      open={formOpen} onCancel={() => setFormOpen(false)} onOk={() => void saveGrant()} okText={formMode === "bulk" ? "预览变更" : "保存"} cancelText="取消" confirmLoading={loading} destroyOnHidden>
      {formMode === "bulk" && <div className="mb-4"><Typography.Paragraph>所选账号将获得相同的业务范围和对照能力。</Typography.Paragraph>
        <Radio.Group aria-label="批量授权方式" value={bulkMode} onChange={e => setBulkMode(e.target.value)}>
          <Radio value="append">追加范围（保留已有授权）</Radio><Radio value="replace">替换全部范围</Radio>
        </Radio.Group>
        {bulkMode === "replace" && <Alert className="!mt-3" type="warning" showIcon message="将删除这些账号在当前工作空间学情库的原有范围，改为本次配置。" />}</div>}
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item name="level" label="业务范围" rules={[{ required: true }]}><Select options={Object.entries(levels).map(([value, label]) => ({ value, label }))}
          onChange={() => { form.setFieldsValue({ capabilities: [], city_id: source?.city_id }); for (const k of Object.keys(fieldLabels).filter(k => k !== "city_id")) form.setFieldValue(k as keyof EduGrantV2, undefined); }} /></Form.Item>
        <Form.Item name="city_id" hidden rules={[{ required: true }]}><Input /></Form.Item>
        {required[level].filter(key => key !== "city_id").map(key => <Form.Item key={key} name={key} label={fieldLabels[key]} rules={[{ required: true, message: `请选择${fieldLabels[key]}` }]}>
          {key === "student_id" ? <Input /> : <Select showSearch options={options(key)} onChange={() => { for (const k of required[level].slice(required[level].indexOf(key) + 1)) form.setFieldValue(k as keyof EduGrantV2, undefined); }} />}
        </Form.Item>)}
        <Form.Item name="capabilities" label="对照能力"><Checkbox.Group options={(level === "class" ? ["grade_baseline"] : level === "district" ? ["city_baseline", "district_self_rank"] : level === "school" ? ["city_baseline"] : []).map(value => ({ value, label: capabilityLabels[value] }))} /></Form.Item>
        <Form.Item name="enabled" valuePropName="checked"><Checkbox>启用授权</Checkbox></Form.Item>
        <details><summary>有效期与委派设置</summary>
          <Form.Item name="can_delegate" valuePropName="checked"><Checkbox>允许在此范围内委派授权（同时需要工作空间管理身份）</Checkbox></Form.Item>
          <Form.Item name="valid_from" label="授权开始时间（含时区，如 2026-11-10T09:00:00+08:00）" rules={[{ validator: (_, value) => !value || !Number.isNaN(Date.parse(value)) ? Promise.resolve() : Promise.reject(new Error("请输入有效时间")) }]}><Input /></Form.Item>
          <Form.Item name="valid_to" label="授权结束时间（留空为长期）" rules={[{ validator: (_, value) => !value || !Number.isNaN(Date.parse(value)) ? Promise.resolve() : Promise.reject(new Error("请输入有效时间")) }]}><Input /></Form.Item>
          {(source?.supports_history || formValues?.history_from || formValues?.history_to) && <>
            <Form.Item name="history_from" label="可查询考试起始日期"><Input type="date" /></Form.Item>
            <Form.Item name="history_to" label="可查询考试截止日期"><Input type="date" /></Form.Item>
          </>}
        </details>
        <Form.Item name="remark" label="备注"><Input maxLength={200} /></Form.Item>
      </Form>
    </Modal>
    <Modal title="确认批量权限变更" open={!!review} width={900} onCancel={() => setReview(undefined)}
      cancelButtonProps={{ disabled: loading }} closable={!loading} maskClosable={!loading} keyboard={!loading}
      onOk={() => void applyReview()} okText={`确认应用 ${review?.changed || 0} 个账号`} cancelText="返回" confirmLoading={loading}>
      <Alert type={review?.operation === "追加范围" ? "info" : "warning"} showIcon className="!mb-3"
        message={`${review?.operation}：已校验 ${review?.changed || 0} 个变更，${(review?.rows.length || 0) - (review?.changed || 0)} 个账号无需变更。提交时再次校验，任一账号冲突则整批不写入。`} />
      <Table<ReviewRow> rowKey="id" size="small" dataSource={review?.rows || []} pagination={{ pageSize: 10 }} scroll={{ x: 850 }}
        columns={[
          { title: "账号", width: 180, render: (_, row) => `${row.name} (${row.account})` },
          { title: "变更前", width: 280, render: (_, row) => grantDetails(row.before) },
          { title: "变更后", width: 280, render: (_, row) => grantDetails(row.after) },
          { title: "操作", width: 110, render: (_, row) => row.changed ? review?.operation : "保持不变" }
        ]} />
    </Modal>
  </Card>;
}

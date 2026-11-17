import { useState } from 'react';
import { ArrowRightOutlined, CheckOutlined, DatabaseOutlined, DownloadOutlined, EditOutlined, PlusOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons';
import { App, Button, Checkbox, DatePicker, Descriptions, Drawer, Form, Input, Modal, Select, Space, Switch, Table, Tabs, Tag } from 'antd';
import dayjs from 'dayjs';
import { alerts, auditRows, exams, initialReports, initialRoles, resources, schools, subjectData } from './data';
import type { DataRecord, Report, Role } from './data';

const selectOptions = (values: string[]) => values.map(value => ({ value }));

function Status({ value }: { value: string }) {
  const color = ['待校验', '一般提醒'].includes(value) ? 'gold' : ['重点关注', '失败'].includes(value) ? 'red' : 'green';
  return <Tag color={color}>{value}</Tag>;
}

export function Resources() {
  const [active, setActive] = useState('数据源');
  const [data, setData] = useState(resources);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>();
  const [detail, setDetail] = useState<DataRecord>();
  const [adding, setAdding] = useState(false);
  const [form] = Form.useForm<Pick<DataRecord, 'name' | 'category' | 'scope'>>();
  const { message } = App.useApp();
  const resource = data[active];
  const matches = resource.rows.filter(row => `${row.name} ${row.scope}`.toLowerCase().includes(query.trim().toLowerCase()) && (!category || row.category === category));
  function save(values: Pick<DataRecord, 'name' | 'category' | 'scope'>) {
    const row: DataRecord = { ...values, id: crypto.randomUUID(), date: dayjs().format('YYYY-MM-DD HH:mm'), status: active === '数据源' ? '待校验' : '启用' };
    setData(previous => ({ ...previous, [active]: { ...resource, rows: [row, ...resource.rows] } }));
    setCategory(undefined);
    setQuery('');
    setAdding(false);
    message.success(`已新增${resource.singular}`);
  }
  function validateConnection() {
    if (!detail) return;
    const next = { ...detail, status: '已连接', date: dayjs().format('YYYY-MM-DD HH:mm') };
    setData(previous => ({ ...previous, [active]: { ...resource, rows: resource.rows.map(row => row.id === next.id ? next : row) } }));
    setDetail(next);
    message.success('模拟连接校验通过');
  }
  return <div className="page-content">
    <section className="paper admin-panel">
      <Tabs activeKey={active} onChange={value => { setActive(value); setCategory(undefined); setQuery(''); }} items={Object.keys(data).map(key => ({ key, label: key }))} />
      <div className="table-toolbar">
        <Space wrap><Input aria-label="搜索名称" prefix={<SearchOutlined />} allowClear placeholder={`搜索${resource.singular}`} value={query} onChange={event => setQuery(event.target.value)} /><Select aria-label="类型筛选" className="type-filter" value={category} allowClear placeholder="全部类型" onChange={setCategory} options={selectOptions(resource.categories)} /></Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setAdding(true); }}>新增{resource.singular}</Button>
      </div>
      <Table<DataRecord> rowKey="id" dataSource={matches} scroll={{ x: 690 }} pagination={{ pageSize: 5, showSizeChanger: false, hideOnSinglePage: true }} columns={[
        { title: '名称', dataIndex: 'name', render: (name: string) => <span className="table-name"><DatabaseOutlined /> {name}</span> },
        { title: '类型', dataIndex: 'category' }, { title: '所属范围', dataIndex: 'scope' },
        { title: '状态', dataIndex: 'status', render: (value: string) => <Status value={value} /> },
        { title: '最近更新', dataIndex: 'date' }, { title: '操作', key: 'actions', render: (_, row) => <Button type="link" onClick={() => setDetail(row)}>查看详情</Button> },
      ]} />
      <div className="table-total">共 {matches.length} 条记录</div>
    </section>
    <Drawer title={`${resource.singular}详情`} open={Boolean(detail)} onClose={() => setDetail(undefined)} extra={active === '数据源' && <Button onClick={validateConnection}>测试连接</Button>}>
      {detail && <Descriptions column={1} items={[{ key: 'name', label: '名称', children: detail.name }, { key: 'category', label: '类型', children: detail.category }, { key: 'scope', label: '所属范围', children: detail.scope }, { key: 'status', label: '状态', children: <Status value={detail.status} /> }, { key: 'date', label: '最近更新', children: detail.date }]} />}
    </Drawer>
    <Modal title={`新增${resource.singular}`} open={adding} onCancel={() => setAdding(false)} okText="保存" onOk={() => form.submit()}>
      <Form form={form} layout="vertical" onFinish={save}>
        <Form.Item name="name" label="名称" rules={[{ required: true, whitespace: true }]}><Input /></Form.Item>
        <Form.Item name="category" label="类型" rules={[{ required: true }]}><Select options={selectOptions(resource.categories)} /></Form.Item>
        <Form.Item name="scope" label="所属范围" rules={[{ required: true, whitespace: true }]}><Input /></Form.Item>
      </Form>
    </Modal>
  </div>;
}

function download(filename: string, content: string) {
  const url = URL.createObjectURL(new Blob(['\ufeff', content], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Analysis() {
  const [tab, setTab] = useState('generate');
  const [reports, setReports] = useState(initialReports);
  const [preview, setPreview] = useState<Report>();
  const [school, setSchool] = useState(schools[0]);
  const [generating, setGenerating] = useState(false);
  const [form] = Form.useForm<Pick<Report, 'exam' | 'scope' | 'type'> & { grade?: string }>();
  const { message } = App.useApp();
  async function generate(values: Pick<Report, 'exam' | 'scope' | 'type'> & { grade?: string }) {
    setGenerating(true);
    await new Promise(resolve => setTimeout(resolve, 600));
    const report = { ...values, id: crypto.randomUUID(), name: `${values.scope === schools[0] ? '' : `${values.scope} · `}${values.exam}${values.grade ? ` · ${values.grade}` : ''}学情报告`, date: dayjs().format('YYYY-MM-DD') };
    setReports(previous => [report, ...previous]);
    setGenerating(false);
    setPreview(report);
  }
  function exportReport(report: Report) {
    download(`${report.name}.txt`, `${report.name}\n分析范围：${report.scope}\n报告类型：${report.type}\n日期：${report.date}\n\n学科达线率（演示数据）\n${subjectData.map(item => `${item.name} ${item.current}%`).join('\n')}`);
    message.success('已下载报告文本');
  }
  return <div className="page-content">
    <section className="paper admin-panel">
      <Tabs activeKey={tab} onChange={setTab} items={[{ key: 'generate', label: '生成报告' }, { key: 'history', label: `报告历史 (${reports.length})` }]} />
      {tab === 'generate' ? <div className="paper report-layout">
        <Form form={form} layout="vertical" initialValues={{ exam: exams[0], scope: schools[0], type: '区域诊断' }} onFinish={generate}>
          <Form.Item name="exam" label="考试批次" rules={[{ required: true }]}><Select options={selectOptions(exams)} /></Form.Item>
          <Form.Item name="scope" label="学校范围" rules={[{ required: true }]}><Select onChange={value => { setSchool(value); form.setFieldValue('grade', undefined); }} options={selectOptions(schools)} /></Form.Item>
          {school !== schools[0] && <Form.Item name="grade" label="年级" rules={[{ required: true }]}><Select placeholder="选择年级" options={selectOptions(['七年级', '八年级', '九年级'])} /></Form.Item>}
          <Form.Item name="type" label="报告类型" rules={[{ required: true }]}><Select options={selectOptions(['区域诊断', '学校教研', '学科对比'])} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={generating}>生成学情报告 <ArrowRightOutlined /></Button>
        </Form>
        <aside className="report-aside"><h3>报告包含</h3><ol><li>考试概况与样本范围</li><li>各学科表现与达线率</li><li>重点问题与教研建议</li></ol></aside>
      </div> : <Table<Report> rowKey="id" dataSource={reports} pagination={false} scroll={{ x: 650 }} columns={[
        { title: '报告名称', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' }, { title: '生成日期', dataIndex: 'date' },
        { title: '操作', key: 'actions', render: (_, report) => <Space><Button type="link" onClick={() => setPreview(report)}>预览</Button><Button type="link" onClick={() => exportReport(report)}>导出</Button></Space> },
      ]} />}
    </section>
    <Drawer title="报告预览" open={Boolean(preview)} onClose={() => setPreview(undefined)} size="large" extra={preview && <Button icon={<DownloadOutlined />} onClick={() => exportReport(preview)}>导出文本</Button>}>
      {preview && <div className="report-preview"><Tag color="blue">{preview.type}</Tag><h2>{preview.name}</h2><p>{preview.scope} · {preview.date}</p><h3>学科达线表现</h3>
        <Table size="small" rowKey="name" dataSource={subjectData} pagination={false} columns={[{ title: '学科', dataIndex: 'name' }, { title: '本次达线率', dataIndex: 'current', render: value => `${value}%` }, { title: '上次达线率', dataIndex: 'previous', render: value => `${value}%` }]} />
        <h3>教研建议</h3><p>优先复盘科学实验探究题和语文阅读题，结合班级得分差异安排下一轮针对性训练。</p></div>}
    </Drawer>
  </div>;
}

const permissionOptions = ['查看问数', '生成报告', '导出报告', '查看数据', '维护数据', '管理成员'];
const members = [
  { id: 'M-01', name: '林老师', role: '区级教研员', school: '区域教研中心' },
  { id: 'M-02', name: '陈老师', role: '校级管理员', school: '滨江实验中学' },
  { id: 'M-03', name: '周老师', role: '学科教师', school: '文澜中学' },
  { id: 'M-04', name: '吴老师', role: '学科教师', school: '江南中学' },
];

export function Permissions() {
  const [roles, setRoles] = useState(initialRoles);
  const [activeRoleId, setActiveRoleId] = useState(initialRoles[0].id);
  const [active, setActive] = useState('roles');
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [editing, setEditing] = useState<Role>();
  const [assignment, setAssignment] = useState(false);
  const [assignedRole, setAssignedRole] = useState<string>();
  const [people, setPeople] = useState(members);
  const [form] = Form.useForm<Pick<Role, 'name' | 'scope' | 'permissions'>>();
  const { message } = App.useApp();
  const activeRole = roles.find(role => role.id === activeRoleId) ?? roles[0];
  function openEditor(role: Role) {
    form.setFieldsValue(role);
    setEditing(role);
  }
  function save(values: Pick<Role, 'name' | 'scope' | 'permissions'>) {
    if (!editing) return;
    const updated = { ...editing, ...values };
    setRoles(previous => previous.some(role => role.id === editing.id) ? previous.map(role => role.id === editing.id ? updated : role) : [...previous, updated]);
    setPeople(previous => previous.map(person => person.role === editing.name ? { ...person, role: values.name } : person));
    setActiveRoleId(updated.id);
    setEditing(undefined);
    message.success('角色权限已保存');
  }
  function assign() {
    if (!assignedRole) return;
    const nextPeople = people.map(person => selected.includes(person.id) ? { ...person, role: assignedRole } : person);
    setRoles(previous => previous.map(role => {
      const oldCount = people.filter(person => person.role === role.name).length;
      const newCount = nextPeople.filter(person => person.role === role.name).length;
      return { ...role, memberCount: role.memberCount + newCount - oldCount };
    }));
    setPeople(nextPeople);
    setAssignment(false);
    setSelected([]);
    message.success('成员角色已更新');
  }
  return <div className="page-content">
    <section className="paper admin-panel">
      <Tabs activeKey={active} onChange={setActive} items={[{ key: 'roles', label: '角色权限' }, { key: 'members', label: '成员授权' }]} />
      {active === 'roles' ? <div className="role-workspace">
        <div className="role-list" role="group" aria-label="角色列表">
          {roles.map(role => (
            <button type="button" key={role.id} className="role-option" aria-pressed={role.id === activeRole.id} onClick={() => setActiveRoleId(role.id)}>
              <span className="role-icon" aria-hidden="true"><SafetyCertificateOutlined /></span>
              <span>{role.name}</span>
            </button>
          ))}
          <Button block type="dashed" icon={<PlusOutlined />} onClick={() => openEditor({ id: crypto.randomUUID(), name: '', scope: '本校', permissions: [], memberCount: 0 })}>新增角色</Button>
        </div>
        <section className="role-detail" aria-label="角色详情">
          <div className="role-detail-heading">
            <h2>{activeRole.name}</h2>
            <Button icon={<EditOutlined />} onClick={() => openEditor(activeRole)}>编辑权限</Button>
          </div>
          <dl className="role-facts">
            <div><dt>数据范围</dt><dd>{activeRole.scope}</dd></div>
            <div><dt>成员数</dt><dd>{activeRole.memberCount}</dd></div>
          </dl>
          <h3>功能权限</h3>
          {activeRole.permissions.length > 0 ? <ul className="role-permissions">
            {activeRole.permissions.map(permission => <li key={permission}><CheckOutlined aria-hidden="true" />{permission}</li>)}
          </ul> : <p className="subtle">暂无功能权限</p>}
        </section>
      </div> : <><div className="table-toolbar"><span>已选 {selected.length} 位成员</span><Button type="primary" disabled={!selected.length} onClick={() => { setAssignedRole(undefined); setAssignment(true); }}>批量分配角色</Button></div>
        <Table rowKey="id" dataSource={people} pagination={{ pageSize: 2 }} rowSelection={{ selectedRowKeys: selected, preserveSelectedRowKeys: true, onChange: setSelected }} columns={[{ title: '成员', dataIndex: 'name' }, { title: '角色', dataIndex: 'role' }, { title: '组织', dataIndex: 'school' }]} /></>}
    </section>
    <Drawer title={editing?.name ? `编辑角色 · ${editing.name}` : '新增角色'} open={Boolean(editing)} onClose={() => setEditing(undefined)} forceRender extra={<Button type="primary" onClick={() => form.submit()}>保存权限</Button>}>
      <Form form={form} layout="vertical" onFinish={save}>
        <Form.Item name="name" label="角色名称" rules={[{ required: true, whitespace: true }, { validator: (_, value) => roles.some(role => role.id !== editing?.id && role.name === value) ? Promise.reject(new Error('已有同名角色')) : Promise.resolve() }]}><Input /></Form.Item>
        <Form.Item name="scope" label="数据范围" rules={[{ required: true }]}><Select options={selectOptions(['全部学校', '本校', '任教学科与班级'])} /></Form.Item>
        <Form.Item name="permissions" label="功能权限"><Checkbox.Group options={permissionOptions} className="permission-options" /></Form.Item>
      </Form>
    </Drawer>
    <Modal title="批量分配角色" open={assignment} okButtonProps={{ disabled: !assignedRole }} onOk={assign} onCancel={() => setAssignment(false)}><p>为已选的 {selected.length} 位成员分配角色。</p><Select aria-label="选择角色" className="full-width" placeholder="选择角色" value={assignedRole} onChange={setAssignedRole} options={selectOptions(roles.map(role => role.name))} /></Modal>
  </div>;
}

export function Audit() {
  const [tab, setTab] = useState('操作日志');
  const [query, setQuery] = useState('');
  const [dates, setDates] = useState<[string, string]>(['', '']);
  const rows = auditRows.filter(row => row.type === tab && `${row.user}${row.action}${row.target}`.includes(query.trim()) && (!dates[0] || row.date.slice(0, 10) >= dates[0]) && (!dates[1] || row.date.slice(0, 10) <= dates[1]));
  return <div className="page-content">
    <section className="paper admin-panel">
      <Tabs activeKey={tab} onChange={setTab} items={['操作日志', '登录日志', '访问日志'].map(key => ({ key, label: key }))} />
      <div className="table-toolbar"><Space wrap><Input aria-label="搜索日志" placeholder="搜索用户、操作或对象" prefix={<SearchOutlined />} allowClear value={query} onChange={event => setQuery(event.target.value)} /><DatePicker.RangePicker onChange={(_, values) => setDates(values)} /></Space><Button icon={<DownloadOutlined />} disabled={!rows.length} onClick={() => download(`${tab}.csv`, ['用户,操作,对象,结果,时间', ...rows.map(row => [row.user, row.action, row.target, row.result, row.date].join(','))].join('\n'))}>导出 CSV</Button></div>
      <Table rowKey="id" dataSource={rows} pagination={false} scroll={{ x: 620 }} columns={[
        { title: '用户', dataIndex: 'user' }, { title: '操作', dataIndex: 'action' }, { title: '对象', dataIndex: 'target' },
        { title: '结果', dataIndex: 'result', render: (value: string) => <Status value={value} /> }, { title: '时间', dataIndex: 'date' },
      ]} />
    </section>
  </div>;
}

const initialRules = [
  { id: 'rate', name: '达线率低于目标', condition: '达线率低于目标 5 个百分点', enabled: true },
  { id: 'gap', name: '班级差异扩大', condition: '同一学科班级差距超过 8 个百分点', enabled: true },
  { id: 'excellent', name: '优秀率下降', condition: '较上次监测下降 3 个百分点', enabled: true },
];

export function Alerts({ route, navigate }: { route: string; navigate: (page: string) => void }) {
  const index = Number(route.split('/')[1]);
  const detail = alerts[index];
  const rulesOpen = route === 'alerts/rules';
  const [rules, setRules] = useState(initialRules);
  return <div className="page-content">
    <section className="paper admin-panel">
      <Tabs activeKey={rulesOpen ? 'rules' : 'inbox'} onChange={value => navigate(value === 'rules' ? 'alerts/rules' : 'alerts')} items={[{ key: 'inbox', label: `全部提醒 (${alerts.length})` }, { key: 'rules', label: '异常规则' }]} />
      {rulesOpen ? <Table rowKey="id" dataSource={rules} pagination={false} scroll={{ x: 500 }} columns={[
        { title: '规则名称', dataIndex: 'name' }, { title: '触发条件', dataIndex: 'condition' },
        { title: '启用', dataIndex: 'enabled', render: (enabled: boolean, rule) => <Switch aria-label={`启用${rule.name}`} checked={enabled} onChange={value => setRules(previous => previous.map(item => item.id === rule.id ? { ...item, enabled: value } : item))} /> },
      ]} /> : <Table rowKey="title" dataSource={alerts} pagination={false} scroll={{ x: 620 }} columns={[
      { title: '提醒', dataIndex: 'title' }, { title: '学校', dataIndex: 'school' }, { title: '等级', dataIndex: 'level', render: (value: string) => <Status value={value} /> },
      { title: '时间', dataIndex: 'time' }, { title: '操作', key: 'actions', render: (_, row) => <Button type="link" onClick={() => navigate(`alerts/${alerts.indexOf(row)}`)}>查看</Button> },
    ]} />}
    </section>
    <Drawer title={detail?.title} open={Boolean(detail)} onClose={() => navigate('alerts')}>
      {detail && <><Status value={detail.level} /><p>{detail.school} · {detail.time}</p><p>{detail.description}</p><Button onClick={() => navigate('analysis')}>前往生成学情报告 <ArrowRightOutlined /></Button></>}
    </Drawer>
  </div>;
}

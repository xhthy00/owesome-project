import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import EduPermissionV2 from "./EduPermissionV2";
import { permissionApi, type EduGrantV2, type EduPermissionUser } from "@/api/permission";

vi.mock("@/api/permission", () => ({ permissionApi: {
  eduV2Source: vi.fn(), eduV2Catalog: vi.fn(), eduV2Users: vi.fn(), getEduV2: vi.fn(), saveEduV2: vi.fn(),
  previewEduV2: vi.fn(), batchEduV2: vi.fn()
} }));
const computedStyle = window.getComputedStyle.bind(window);
const existing: EduGrantV2 = { id: "old", workspace_oid: 1, datasource_id: 4, level: "class", city_id: "C1",
  school_id: "S1", cohort_id: "G1", class_id: "B0", enabled: true, capabilities: [] };
const users: EduPermissionUser[] = [
  { id: 7, account: "teacher", name: "教师", status: 1, manageable: true, managed: true, active_count: 1,
    document: { schema_version: 2, revision: 7, grants: [existing] } },
  { id: 8, account: "teacher2", name: "教师二", status: 1, manageable: true, managed: false, active_count: 0,
    document: { schema_version: 2, revision: 9, grants: [] } }
];
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => computedStyle(element));
  vi.mocked(permissionApi.eduV2Source).mockResolvedValue({ datasource_id: 4, workspace_oid: 1, name: "学情业务库",
    source_type: "DATABASE_EDU_URL", city_id: "C1", city_label: "全市", supports_history: false });
  vi.mocked(permissionApi.eduV2Catalog).mockResolvedValue({ workspace_oid: 1, datasource_id: 4,
    items: [{ city_id: "C1", school_id: "S1", cohort_id: "G1", class_id: "B1" }] });
  vi.mocked(permissionApi.eduV2Users).mockResolvedValue({ items: users, total: 602, page: 1, page_size: 20 });
  vi.mocked(permissionApi.getEduV2).mockResolvedValue({ schema_version: 2, revision: 7, grants: [] });
  vi.mocked(permissionApi.saveEduV2).mockResolvedValue({ schema_version: 2, revision: 8, grants: [] });
  vi.mocked(permissionApi.batchEduV2).mockImplementation(async (_, __, apply) => ({ validated: 2, applied: apply ? 2 : 0 }));
});
async function select(label: string, value: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: new RegExp(label) }));
  const options = await screen.findAllByText(value);
  fireEvent.click(options.find(element => element.classList.contains("ant-select-item-option-content")) || options[0]);
}
async function chooseClass() {
  await select("学校", "S1"); await select("届次", "G1"); await select("班级", "B1");
}
async function chooseUser() {
  fireEvent.click(await screen.findByRole("button", { name: "管理权限 teacher" }));
  await waitFor(() => expect(permissionApi.getEduV2).toHaveBeenCalledWith(7, 4));
  await waitFor(() => expect(screen.getByRole("button", { name: "添加范围" })).toBeEnabled());
}
function parseCsv(text: string) {
  return text.split("\n").slice(1).map(line => {
    const cells = [...line.matchAll(/"((?:[^"]|"")*)"(?:,|$)/g)].map(match => match[1].replaceAll('""', '"'));
    return { id: Number(cells[0]), revision: Number(cells[1]), grants: JSON.parse(cells[2]) as EduGrantV2[] };
  });
}

it("reports a missing education connection without a separate datasource mapping", async () => {
  vi.mocked(permissionApi.eduV2Source).mockRejectedValue(new Error("尚未配置学情库 DATABASE_EDU_URL"));
  render(<EduPermissionV2 />);
  expect(await screen.findByText(/尚未配置学情库 DATABASE_EDU_URL/)).toBeVisible();
  expect(screen.getByRole("button", { name: "批量授权" })).toBeDisabled();
});

it("saves an individual class grant against the latest loaded revision", async () => {
  render(<EduPermissionV2 />);
  await chooseUser();
  expect(screen.queryByLabelText("分级权限用户")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "添加范围" }));
  await chooseClass();
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(permissionApi.saveEduV2).toHaveBeenCalledWith(7, 4, 7, [expect.objectContaining({
    workspace_oid: 1, datasource_id: 4, level: "class", city_id: "C1", school_id: "S1",
    cohort_id: "G1", class_id: "B1", enabled: true, capabilities: []
  })]));
}, 15000);

it("previews shared scope grants, preserving each user's existing grants before atomic apply", async () => {
  render(<EduPermissionV2 />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择账号 teacher" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "选择账号 teacher2" }));
  fireEvent.click(screen.getByRole("button", { name: "批量授权" }));
  await chooseClass();
  fireEvent.click(screen.getByRole("button", { name: "预览变更" }));
  await screen.findByText("确认批量权限变更");
  expect(permissionApi.batchEduV2).toHaveBeenCalledTimes(1);
  const [source, csv, apply] = vi.mocked(permissionApi.batchEduV2).mock.calls[0];
  expect(source).toBe(4); expect(apply).toBe(false);
  const rows = parseCsv(csv);
  expect(rows.map(row => [row.id, row.revision, row.grants.length])).toEqual([[7, 7, 2], [8, 9, 1]]);
  expect(rows[0].grants[0]).toEqual(existing);
  expect(rows[0].grants[1]).toMatchObject({ school_id: "S1", cohort_id: "G1", class_id: "B1" });
  expect(rows[0].grants[1].id).not.toEqual(rows[1].grants[0].id);
  expect(permissionApi.saveEduV2).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认应用 2 个账号" }));
  await waitFor(() => expect(permissionApi.batchEduV2).toHaveBeenLastCalledWith(4, csv, true));
  await waitFor(() => expect(screen.getByRole("button", { name: "批量授权" })).toBeDisabled());
}, 20000);

it("keeps cross-page selections while searching server-side", async () => {
  vi.mocked(permissionApi.eduV2Users).mockResolvedValueOnce({ items: [users[0]], total: 602, page: 1, page_size: 20 })
    .mockResolvedValue({ items: [users[1]], total: 1, page: 1, page_size: 20 });
  render(<EduPermissionV2 />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择账号 teacher" }));
  const search = screen.getByRole("searchbox", { name: "搜索权限账号" });
  fireEvent.change(search, { target: { value: "教师二" } });
  fireEvent.click(screen.getByRole("button", { name: /搜\s*索/ }));
  await waitFor(() => expect(permissionApi.eduV2Users).toHaveBeenLastCalledWith(1, 20, "教师二"));
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择账号 teacher2" }));
  expect(screen.getByText(/已选择 2 个/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "批量撤销" }));
  await screen.findByText("确认批量权限变更");
  const rows = parseCsv(vi.mocked(permissionApi.batchEduV2).mock.calls[0][1]);
  // Unconfigured users also get an explicit deny marker, preventing legacy fallback.
  expect(rows.map(row => row.id)).toEqual([7, 8]);
  expect(rows[0].grants).toEqual([]);
}, 15000);

it("does not add an equivalent grant twice", async () => {
  vi.mocked(permissionApi.eduV2Users).mockResolvedValue({ items: [{ ...users[0],
    document: { schema_version: 2, revision: 7, grants: [{ ...existing, class_id: "B1" }] } }], total: 1, page: 1, page_size: 20 });
  render(<EduPermissionV2 />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择账号 teacher" }));
  fireEvent.click(screen.getByRole("button", { name: "批量授权" }));
  await chooseClass();
  fireEvent.click(screen.getByRole("button", { name: "预览变更" }));
  expect(await screen.findByText("所选账号已具有相同配置，无需重复授权")).toBeVisible();
  expect(permissionApi.batchEduV2).not.toHaveBeenCalled();
}, 15000);

it("does not allow a failed preview to be applied", async () => {
  vi.mocked(permissionApi.batchEduV2).mockRejectedValue(new Error("权限已被修改，请刷新后重试"));
  render(<EduPermissionV2 />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择账号 teacher" }));
  fireEvent.click(screen.getByRole("button", { name: "批量撤销" }));
  expect(await screen.findByText("权限已被修改，请刷新后重试")).toBeVisible();
  expect(screen.queryByRole("button", { name: /确认应用/ })).not.toBeInTheDocument();
  expect(permissionApi.batchEduV2).toHaveBeenCalledTimes(1);
});

it("disables selection and editing outside the manager's scope", async () => {
  vi.mocked(permissionApi.eduV2Users).mockResolvedValue({ items: [{ ...users[0], manageable: false, document: undefined,
    message: "授权超出管理范围或配置异常，请联系上级管理员" }], total: 1, page: 1, page_size: 20 });
  render(<EduPermissionV2 />);
  expect(await screen.findByRole("checkbox", { name: "选择账号 teacher" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "管理权限 teacher" })).toBeDisabled();
});

it("requires preview again after changing the CSV content", async () => {
  render(<EduPermissionV2 />);
  await screen.findByRole("button", { name: "管理权限 teacher" });
  fireEvent.click(screen.getByText("高级：差异化 CSV 导入"));
  const input = screen.getByLabelText("分级权限CSV");
  fireEvent.change(input, { target: { value: "user_id,expected_revision,grants_json\n7,7,[]" } });
  const apply = screen.getByRole("button", { name: "应用已校验批次" });
  expect(apply).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "校验导入" }));
  await waitFor(() => expect(apply).toBeEnabled());
  fireEvent.change(input, { target: { value: "changed" } });
  expect(apply).toBeDisabled();
  expect(permissionApi.batchEduV2).toHaveBeenCalledWith(4, "user_id,expected_revision,grants_json\n7,7,[]", false);
});

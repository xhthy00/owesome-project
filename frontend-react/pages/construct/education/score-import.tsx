import {
  CheckCircleOutlined,
  DownloadOutlined,
  ReloadOutlined,
  UploadOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Select,
  Space,
  Steps,
  Table,
  Typography,
  Upload,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { UploadProps } from "antd";
import { useEffect, useMemo, useState } from "react";
import {
  educationApi,
  type ScoreImportErrorRow,
  type ScoreImportResult,
  type ScoreImportType
} from "@/api/education";
import { datasourceApi, type DatasourceItem } from "@/api/datasource";

type WizardStep = 0 | 1 | 2;

type StepImportState = {
  file: File | null;
  preview: ScoreImportResult | null;
  doneMessage: string;
  done: boolean;
};

const emptyImportState = (): StepImportState => ({
  file: null,
  preview: null,
  doneMessage: "",
  done: false
});

const STEP_META: Array<{ title: string; description: string; importType?: ScoreImportType }> = [
  { title: "选择数据源", description: "指定成绩写入的目标库" },
  { title: "总分成绩导入", description: "导入学生总分", importType: "total" },
  { title: "成绩明细导入", description: "导入小题得分明细", importType: "detail" }
];

export default function ScoreImportPage() {
  const [step, setStep] = useState<WizardStep>(0);
  const [datasources, setDatasources] = useState<DatasourceItem[]>([]);
  const [datasourceId, setDatasourceId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [totalState, setTotalState] = useState<StepImportState>(emptyImportState);
  const [detailState, setDetailState] = useState<StepImportState>(emptyImportState);

  const selectedDs = useMemo(
    () => datasources.find((d) => d.id === datasourceId) || null,
    [datasources, datasourceId]
  );

  const loadDatasources = async () => {
    setLoading(true);
    try {
      const res = await datasourceApi.list({ limit: 200 });
      const items = res.items || [];
      setDatasources(items);
      if (!datasourceId && items.length > 0) {
        setDatasourceId(items[0].id);
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "加载数据源失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadDatasources();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const errorColumns: ColumnsType<ScoreImportErrorRow> = useMemo(
    () => [
      { title: "行号", dataIndex: "row", width: 80 },
      { title: "字段", dataIndex: "field", width: 120 },
      { title: "说明", dataIndex: "message" }
    ],
    []
  );

  const buildFormData = (importType: ScoreImportType, file: File) => {
    if (!datasourceId) {
      throw new Error("请先选择数据源");
    }
    const fd = new FormData();
    fd.append("datasource_id", String(datasourceId));
    fd.append("import_type", importType);
    fd.append("file", file);
    return fd;
  };

  const runPreview = async (importType: ScoreImportType) => {
    const state = importType === "total" ? totalState : detailState;
    const setState = importType === "total" ? setTotalState : setDetailState;
    if (!state.file) {
      message.warning("请先上传 Excel 文件");
      return;
    }
    try {
      setLoading(true);
      setState((prev) => ({ ...prev, doneMessage: "", done: false }));
      const res = await educationApi.postScoreImport(
        "preview",
        buildFormData(importType, state.file)
      );
      if (!res.data) {
        message.error(res.message || "预览失败");
        return;
      }
      setState((prev) => ({ ...prev, preview: res.data }));
      if (res.data.error_rows.length > 0) {
        message.warning(`校验完成：${res.data.valid_rows}/${res.data.total_rows} 行通过`);
      } else {
        message.success(`校验通过：共 ${res.data.valid_rows} 行`);
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "预览失败");
    } finally {
      setLoading(false);
    }
  };

  const runExecute = async (importType: ScoreImportType) => {
    const state = importType === "total" ? totalState : detailState;
    const setState = importType === "total" ? setTotalState : setDetailState;
    if (!state.file || !state.preview || state.preview.error_rows.length > 0) {
      message.warning("请先预览并确保无错误行");
      return;
    }
    try {
      setLoading(true);
      const res = await educationApi.postScoreImport(
        "execute",
        buildFormData(importType, state.file)
      );
      if (!res.ok) {
        if (res.data) {
          setState((prev) => ({ ...prev, preview: res.data, done: false }));
        }
        message.error(res.message || "导入失败");
        return;
      }
      const s = res.data?.summary;
      const tip = s
        ? `导入完成：更新 ${s.updated} 条${s.students_created ? `，新增学生 ${s.students_created} 人` : ""}`
        : res.message || "导入成功";
      setState((prev) => ({
        ...prev,
        preview: res.data || prev.preview,
        doneMessage: tip,
        done: true
      }));
      message.success(tip);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "导入失败");
    } finally {
      setLoading(false);
    }
  };

  const makeUploadProps = (importType: ScoreImportType): UploadProps => ({
    accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    showUploadList: false,
    beforeUpload: (f) => {
      const setState = importType === "total" ? setTotalState : setDetailState;
      setState({
        file: f,
        preview: null,
        doneMessage: "",
        done: false
      });
      return false;
    }
  });

  const renderPreviewCard = (state: StepImportState) => {
    if (!state.preview) return null;
    const preview = state.preview;
    const sampleKeys = preview.preview_sample?.[0] ? Object.keys(preview.preview_sample[0]) : [];
    const sampleColumns = sampleKeys.map((k) => ({ title: k, dataIndex: k, key: k }));
    return (
      <Card
        className="mt-4"
        size="small"
        title={`校验结果：${preview.valid_rows} / ${preview.total_rows} 行通过`}
      >
        {(preview.summary?.students_to_create || 0) > 0 ? (
          <Alert
            className="mb-3"
            type="info"
            showIcon
            message={`将自动新增 ${preview.summary.students_to_create} 名学生到学生名册`}
          />
        ) : null}
        {preview.error_rows.length > 0 ? (
          <Table<ScoreImportErrorRow>
            className="mb-3"
            size="small"
            rowKey={(r) => `${r.row}-${r.field}`}
            columns={errorColumns}
            dataSource={preview.error_rows}
            pagination={{ pageSize: 8 }}
          />
        ) : (
          <Alert className="mb-3" type="success" showIcon message="全部行校验通过，可执行导入" />
        )}
        {preview.preview_sample.length > 0 ? (
          <>
            <Typography.Text className="mb-2 block">预览样本（前 10 行）</Typography.Text>
            <Table
              size="small"
              rowKey={(_, i) => String(i)}
              columns={sampleColumns}
              dataSource={preview.preview_sample}
              pagination={false}
              scroll={{ x: true }}
            />
          </>
        ) : null}
      </Card>
    );
  };

  const renderImportStep = (importType: ScoreImportType) => {
    const state = importType === "total" ? totalState : detailState;
    const canExecute = Boolean(
      state.preview && state.preview.error_rows.length === 0 && state.preview.valid_rows > 0
    );
    const title =
      importType === "total"
        ? "上传总分成绩 Excel（脱敏成绩_仅总分.xlsx）"
        : "上传小题分明细 Excel（脱敏成绩_小题分明细.xlsx）";

    return (
      <div>
        <Alert
          className="mb-4"
          type="info"
          showIcon
          message={
            importType === "total"
              ? "本步导入学生总分。学号不存在时会自动新增学生；请先完成总分导入，再导入明细。"
              : "本步导入小题得分明细。建议在总分导入成功后再执行，便于后续学情分析关联。"
          }
        />
        <Card>
          <Typography.Text className="mb-3 block font-medium">{title}</Typography.Text>
          <Space wrap>
            <Button
              icon={<DownloadOutlined />}
              onClick={() =>
                void educationApi
                  .downloadTemplate(importType)
                  .catch((e) => message.error(e instanceof Error ? e.message : "下载失败"))
              }
            >
              下载模板
            </Button>
            <Upload {...makeUploadProps(importType)}>
              <Button icon={<UploadOutlined />}>{state.file ? state.file.name : "上传 Excel"}</Button>
            </Upload>
            <Button
              loading={loading}
              disabled={!state.file}
              onClick={() => void runPreview(importType)}
            >
              预览校验
            </Button>
            <Button
              type="primary"
              loading={loading}
              disabled={!canExecute}
              onClick={() => void runExecute(importType)}
            >
              确认导入
            </Button>
          </Space>
          {state.doneMessage ? (
            <Alert
              className="mt-4"
              type="success"
              showIcon
              icon={<CheckCircleOutlined />}
              message={state.doneMessage}
              action={
                importType === "total" ? (
                  <Button type="primary" onClick={() => setStep(2)}>
                    下一步
                  </Button>
                ) : undefined
              }
            />
          ) : null}
          {renderPreviewCard(state)}
        </Card>
      </div>
    );
  };

  const goNext = () => {
    if (step === 0) {
      if (!datasourceId) {
        message.warning("请选择数据源");
        return;
      }
      setStep(1);
      return;
    }
    if (step === 1) {
      if (!totalState.done) {
        message.warning("请先完成总分成绩导入，或确认导入成功后再进入下一步");
        return;
      }
      setStep(2);
    }
  };

  const goPrev = () => {
    if (step === 0) return;
    setStep((step - 1) as WizardStep);
  };

  const resetWizard = () => {
    setStep(0);
    setTotalState(emptyImportState());
    setDetailState(emptyImportState());
    message.success("已重置向导");
  };

  return (
    <div className="dbgpt-ui-font p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Typography.Title level={4} className="!mb-1">
            成绩导入
          </Typography.Title>
          <Typography.Text className="oc-muted">
            按向导依次完成：选择数据源 → 总分成绩导入 → 成绩明细导入
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void loadDatasources()} loading={loading}>
            刷新数据源
          </Button>
          <Button onClick={resetWizard}>重新开始</Button>
        </Space>
      </div>

      <Card className="mb-4">
        <Steps
          current={step}
          items={STEP_META.map((s, idx) => ({
            title: s.title,
            description:
              idx === 1 && totalState.done
                ? "已完成"
                : idx === 2 && detailState.done
                  ? "已完成"
                  : s.description
          }))}
        />
      </Card>

      {step === 0 ? (
        <Card title="第一步：选择数据源">
          <Space direction="vertical" size="middle" className="w-full max-w-xl">
            <div>
              <Typography.Text className="mb-1 block">数据源</Typography.Text>
              <Select
                className="w-full"
                placeholder="选择数据源"
                value={datasourceId ?? undefined}
                onChange={(v) => {
                  setDatasourceId(v);
                  setTotalState(emptyImportState());
                  setDetailState(emptyImportState());
                }}
                options={datasources.map((d) => ({
                  value: d.id,
                  label: `${d.name} (${d.type})`
                }))}
              />
            </div>
            {selectedDs ? (
              <Alert
                type="success"
                showIcon
                message={`已选择：${selectedDs.name}（${selectedDs.type}）`}
              />
            ) : (
              <Alert type="warning" showIcon message="请选择一个可用数据源后继续" />
            )}
          </Space>
        </Card>
      ) : null}

      {step === 1 ? renderImportStep("total") : null}
      {step === 2 ? renderImportStep("detail") : null}

      <div className="mt-4 flex justify-between">
        <Button disabled={step === 0} onClick={goPrev}>
          上一步
        </Button>
        <Space>
          {step === 1 && !totalState.done ? (
            <Button
              onClick={() => {
                message.info("已跳过总分导入，请确保库中已有对应总分数据");
                setStep(2);
              }}
            >
              跳过总分，去导明细
            </Button>
          ) : null}
          {step === 0 ? (
            <Button type="primary" onClick={goNext} disabled={!datasourceId}>
              下一步
            </Button>
          ) : null}
          {step === 1 ? (
            <Button type="primary" onClick={goNext} disabled={!totalState.done}>
              下一步
            </Button>
          ) : null}
          {step === 2 ? (
            <Button
              type="primary"
              disabled={!detailState.done}
              onClick={() => message.success("成绩导入流程已完成")}
            >
              完成
            </Button>
          ) : null}
        </Space>
      </div>
    </div>
  );
}

import { DatabaseOutlined, SearchOutlined } from "@ant-design/icons";
import { Popover } from "antd";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import { datasourceApi, type DatasourceItem } from "@/api/datasource";

type DatasourcePickerProps = {
  value: number;
  onChange: (id: number) => void;
  /** 聊天栏内嵌样式（更紧凑） */
  compact?: boolean;
};

export default function DatasourcePicker({ value, onChange, compact = false }: DatasourcePickerProps) {
  const router = useRouter();
  const [datasources, setDatasources] = useState<DatasourceItem[]>([]);
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const res = await datasourceApi.list({ limit: 100 });
        setDatasources(res.items || []);
      } catch {
        setDatasources([]);
      }
    };
    void load();
  }, []);

  useEffect(() => {
    if (datasources.length && !datasources.some((item) => item.id === value)) {
      onChange(datasources[0].id);
    }
  }, [datasources, value, onChange]);

  const selected = useMemo(() => datasources.find((item) => item.id === value), [datasources, value]);

  const filtered = useMemo(() => {
    const key = keyword.trim().toLowerCase();
    if (!key) return datasources;
    return datasources.filter((item) => `${item.name} ${item.type}`.toLowerCase().includes(key));
  }, [datasources, keyword]);

  const panel = (
    <div className="w-[280px]">
      <div className="rounded-lg border border-[#e5e7eb] bg-white p-2 dark:border-[#34384a] dark:bg-[#1f2430]">
        <div className="mb-2 flex items-center rounded-lg border border-[#e5e7eb] bg-[#f8fafc] px-2 dark:border-[#34384a] dark:bg-[#232734]">
          <SearchOutlined className="mr-2 text-xs text-[#94a3b8]" />
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索数据库"
            className="h-7 w-full border-0 bg-transparent text-xs text-[#334155] outline-none placeholder:text-[#94a3b8] dark:text-[#cbd5e1]"
          />
        </div>
        <div className="max-h-52 overflow-y-auto">
          {filtered.length ? (
            filtered.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  onChange(item.id);
                  setOpen(false);
                }}
                className={`mb-1 flex w-full items-center justify-between rounded-lg px-2 py-2 text-left text-xs transition-colors ${
                  item.id === value
                    ? "bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-300"
                    : "text-[#334155] hover:bg-[#f8fafc] dark:text-[#cbd5e1] dark:hover:bg-[#232734]"
                }`}
              >
                <span className="truncate">{item.name}</span>
                <span className="ml-2 text-[10px] opacity-70">{item.type}</span>
              </button>
            ))
          ) : (
            <div className="flex h-32 flex-col items-center justify-center text-[#94a3b8]">
              <DatabaseOutlined className="mb-2 text-2xl opacity-60" />
              <span className="text-xs">暂无可用数据库</span>
            </div>
          )}
        </div>
      </div>
      <div className="mt-1 flex items-center justify-between px-1 text-[10px] text-[#94a3b8]">
        <span>{filtered.length} 个数据库可用</span>
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            void router.push("/construct/database");
          }}
          className="text-[#3b82f6] hover:underline"
        >
          管理数据库 -&gt;
        </button>
      </div>
    </div>
  );

  const trigger = (
    <button
      type="button"
      className={`truncate text-left text-xs text-[#64748b] dark:text-[#a2aec2] ${
        compact ? "max-w-[160px]" : "max-w-[240px]"
      }`}
    >
      {selected ? selected.name : "选择数据源"}
    </button>
  );

  if (compact) {
    return (
      <div className="ml-1 flex h-7 items-center gap-1.5 rounded-full border border-[#e5eaf3] bg-[#f6f8fc] px-2.5 dark:border-[#3a404d] dark:bg-[#242834]">
        <DatabaseOutlined className="text-[11px] text-[#64748b] dark:text-[#a2aec2]" />
        <Popover trigger="click" placement="topLeft" open={open} onOpenChange={setOpen} content={panel} overlayClassName="db-picker-overlay">
          {trigger}
        </Popover>
      </div>
    );
  }

  return (
    <div className="flex h-8 items-center gap-2 rounded-full border border-[#e5eaf3] bg-[#f6f8fc] px-3 text-[#64748b] dark:border-[#3a404d] dark:bg-[#242834] dark:text-[#a2aec2]">
      <DatabaseOutlined className="text-sm" />
      <Popover trigger="click" placement="topLeft" open={open} onOpenChange={setOpen} content={panel} overlayClassName="db-picker-overlay">
        {trigger}
      </Popover>
    </div>
  );
}

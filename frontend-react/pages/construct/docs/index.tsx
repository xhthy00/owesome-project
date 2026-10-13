import {
  ArrowLeftOutlined,
  DownloadOutlined,
  FileProtectOutlined,
  PlayCircleOutlined,
  ReadOutlined,
  ReloadOutlined,
  RightOutlined
} from "@ant-design/icons";
import { Button, message } from "antd";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useRef, useState } from "react";
import { getApiBaseUrl } from "@/api/client";
import { fetchSystemDocFile, listSystemDocs, type SystemDoc } from "@/api/systemDocs";

const DOC_ICON = {
  commitment: FileProtectOutlined,
  training: PlayCircleOutlined,
  manual: ReadOutlined
} as const;

const DOC_TONE = {
  commitment: "bg-[#e8f1ff] text-[#1d4ed8] dark:bg-[#1e3a5f] dark:text-[#93c5fd]",
  training: "bg-[#f3e8ff] text-[#7e22ce] dark:bg-[#3b2763] dark:text-[#d8b4fe]",
  manual: "bg-[#e7f6ef] text-[#047857] dark:bg-[#14352c] dark:text-[#6ee7b7]"
} as const;

function saveBlob(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function kindLabel(kind: SystemDoc["kind"]) {
  return kind === "video" ? "视频" : "Word 文档";
}

export default function SystemDocsPage() {
  const router = useRouter();
  const hostRef = useRef<HTMLDivElement>(null);
  const blobRef = useRef<Blob | null>(null);
  const [docs, setDocs] = useState<SystemDoc[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const [downloading, setDownloading] = useState(false);

  const rawDoc = router.query.doc;
  const docKey = Array.isArray(rawDoc) ? rawDoc[0] : rawDoc;
  const current = useMemo(
    () => (docKey ? docs.find((item) => item.key === docKey) ?? null : null),
    [docs, docKey]
  );

  useEffect(() => {
    let active = true;
    setLoadingCatalog(true);
    setCatalogError("");
    void listSystemDocs()
      .then((items) => {
        if (active) setDocs(items);
      })
      .catch((err: unknown) => {
        if (active) setCatalogError(err instanceof Error ? err.message : "加载系统文档失败");
      })
      .finally(() => {
        if (active) setLoadingCatalog(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!current || current.kind !== "docx") {
      blobRef.current = null;
      return;
    }
    const host = hostRef.current;
    if (!host) return;
    let cancelled = false;
    host.replaceChildren();
    blobRef.current = null;
    setPreviewError("");
    setLoadingPreview(true);
    void (async () => {
      try {
        const blob = await fetchSystemDocFile(current.key);
        if (cancelled) return;
        blobRef.current = blob;
        const { renderAsync } = await import("docx-preview");
        if (cancelled || !hostRef.current) return;
        await renderAsync(blob, hostRef.current, undefined, {
          className: "docx",
          inWrapper: true,
          breakPages: true,
          ignoreWidth: false,
          ignoreHeight: false
        });
      } catch (err: unknown) {
        if (!cancelled) {
          host.replaceChildren();
          setPreviewError(err instanceof Error ? err.message : "文档暂时无法预览");
        }
      } finally {
        if (!cancelled) setLoadingPreview(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [current, previewAttempt]);

  const download = async () => {
    if (!current) return;
    if (current.kind === "video") {
      const sameOrigin = new URL(current.url, window.location.href).origin === window.location.origin;
      const link = document.createElement("a");
      link.href = sameOrigin ? current.url : `${getApiBaseUrl()}/system/docs/${current.key}/file`;
      link.download = current.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      message.success("已开始下载，请到浏览器下载栏查看");
      return;
    }
    setDownloading(true);
    try {
      const blob = blobRef.current ?? (await fetchSystemDocFile(current.key));
      blobRef.current = blob;
      saveBlob(blob, current.filename);
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : "下载失败，请稍后重试");
    } finally {
      setDownloading(false);
    }
  };

  const Icon = DOC_ICON[docKey as keyof typeof DOC_ICON] ?? ReadOutlined;

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f3f6fb] dark:bg-[#10151d]">
      <style jsx global>{`
        .sys-doc-host .docx-wrapper {
          background: transparent !important;
          padding: 8px 0 36px;
        }
        .sys-doc-host .docx-wrapper > section.docx {
          margin-bottom: 20px !important;
          box-shadow: 0 16px 40px rgba(15, 23, 42, 0.08);
        }
      `}</style>

      {docKey ? (
        <div className="shrink-0 border-b border-[#e6ebf2] bg-white px-6 py-5 dark:border-[#243044] dark:bg-[#141b26] md:px-8">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <Link
                href="/construct/docs"
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-[#64748b] transition-colors hover:bg-[#eef3f9] hover:text-[#1d4ed8] dark:hover:bg-[#1c2636]"
                aria-label="返回文档列表"
              >
                <ArrowLeftOutlined />
              </Link>
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-[#e8f1ff] text-xl text-[#1d4ed8] dark:bg-[#1e3a5f] dark:text-[#93c5fd]">
                <Icon />
              </span>
              <div className="min-w-0">
                <h1 className="truncate text-lg font-semibold text-[#1c2533] dark:text-white">
                  {current?.title || "系统文档"}
                </h1>
                <p className="mt-0.5 text-sm text-[#7d8ba2]">
                  {current?.summary || "在线预览，也可下载原文件"}
                </p>
              </div>
            </div>
            <Button
              type="primary"
              size="large"
              icon={<DownloadOutlined />}
              loading={downloading}
              disabled={!current}
              onClick={() => void download()}
              className="!h-10 !rounded-xl !px-5"
            >
              下载
            </Button>
          </div>
        </div>
      ) : (
        <div className="shrink-0 px-6 pb-2 pt-8 md:px-8">
          <div className="mx-auto max-w-5xl">
            <h1 className="text-xl font-semibold text-[#1c2533] dark:text-white">系统文档</h1>
            <p className="mt-1 text-sm text-[#7d8ba2]">承诺书、培训视频和用户手册，点击卡片即可查看或下载。</p>
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto px-6 py-6 md:px-8">
        <div className="mx-auto max-w-5xl">
          {catalogError ? (
            <div className="rounded-2xl border border-[#e6ebf2] bg-white px-6 py-16 text-center shadow-sm dark:border-[#243044] dark:bg-[#141b26]">
              <p className="text-sm text-[#64748b]">{catalogError}</p>
              <Button className="mt-4" icon={<ReloadOutlined />} onClick={() => router.reload()}>
                重新加载
              </Button>
            </div>
          ) : loadingCatalog ? (
            docKey ? (
              <div className="rounded-2xl border border-[#e6ebf2] bg-white px-6 py-20 text-center text-sm text-[#7d8ba2] shadow-sm dark:border-[#243044] dark:bg-[#141b26]">
                正在打开文档…
              </div>
            ) : (
              <div className="grid gap-5 md:grid-cols-3">
                {Array.from({ length: 3 }).map((_, index) => (
                  <div
                    key={index}
                    className="h-52 animate-pulse rounded-2xl border border-[#e6ebf2] bg-white dark:border-[#243044] dark:bg-[#141b26]"
                  />
                ))}
              </div>
            )
          ) : !docKey ? (
            <div className="grid gap-5 md:grid-cols-3">
              {docs.map((item) => {
                const CardIcon = DOC_ICON[item.key as keyof typeof DOC_ICON] ?? ReadOutlined;
                const tone = DOC_TONE[item.key as keyof typeof DOC_TONE] ?? DOC_TONE.manual;
                return (
                  <Link
                    key={item.key}
                    href={`/construct/docs?doc=${item.key}`}
                    className="group flex min-h-52 flex-col rounded-2xl border border-[#e6ebf2] bg-white p-6 shadow-sm transition duration-200 hover:-translate-y-0.5 hover:border-[#c9d8f2] hover:shadow-[0_16px_40px_rgba(15,23,42,0.08)] dark:border-[#243044] dark:bg-[#141b26] dark:hover:border-[#35507a]"
                  >
                    <span className={`flex h-12 w-12 items-center justify-center rounded-2xl text-xl ${tone}`}>
                      <CardIcon />
                    </span>
                    <h2 className="mt-5 text-base font-semibold text-[#1c2533] dark:text-white">{item.title}</h2>
                    <p className="mt-2 flex-1 text-sm leading-6 text-[#7d8ba2]">{item.summary}</p>
                    <div className="mt-6 flex items-center justify-between text-sm">
                      <span className="rounded-full bg-[#f3f6fb] px-2.5 py-1 text-xs text-[#64748b] dark:bg-[#1c2636] dark:text-[#94a3b8]">
                        {kindLabel(item.kind)}
                      </span>
                      <span className="inline-flex items-center gap-1 font-medium text-[#1d4ed8] dark:text-[#93c5fd]">
                        查看
                        <RightOutlined className="text-xs transition-transform group-hover:translate-x-0.5" />
                      </span>
                    </div>
                  </Link>
                );
              })}
            </div>
          ) : !current ? (
            <div className="rounded-2xl border border-[#e6ebf2] bg-white px-6 py-16 text-center shadow-sm dark:border-[#243044] dark:bg-[#141b26]">
              <p className="text-sm text-[#64748b]">没有找到这份文档</p>
              <Link href="/construct/docs" className="mt-4 inline-block text-sm text-[#1d4ed8]">
                返回文档列表
              </Link>
            </div>
          ) : current.kind === "video" ? (
            <div className="overflow-hidden rounded-2xl bg-[#0f172a] shadow-[0_20px_50px_rgba(15,23,42,0.18)]">
              <video
                key={current.url}
                className="aspect-video w-full bg-black"
                controls
                playsInline
                preload="metadata"
                src={current.url}
              />
            </div>
          ) : (
            <div className="min-h-[480px]">
              {loadingPreview ? (
                <div className="py-16 text-center text-sm text-[#7d8ba2]">正在准备预览…</div>
              ) : null}
              {previewError ? (
                <div className="rounded-2xl border border-[#e6ebf2] bg-white px-6 py-16 text-center shadow-sm dark:border-[#243044] dark:bg-[#141b26]">
                  <p className="text-sm text-[#64748b]">{previewError}</p>
                  <p className="mt-2 text-xs text-[#94a3b8]">仍可点击右上角下载原文件</p>
                  <Button className="mt-4" icon={<ReloadOutlined />} onClick={() => setPreviewAttempt((n) => n + 1)}>
                    重新预览
                  </Button>
                </div>
              ) : null}
              <div ref={hostRef} className="sys-doc-host" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

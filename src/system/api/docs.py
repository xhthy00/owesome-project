"""系统文档接口：目录来自配置，Word 文件经服务端转发以便页面内预览。"""

import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from common.core.config import get_settings
from common.exceptions.base import BadRequestException, NotFoundException
from common.schemas.response import success_response
from system.api.auth_deps import get_current_user
from system.docs_catalog import build_doc_catalog, find_doc

router = APIRouter(prefix="/system", tags=["system"])

_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "video": "video/mp4",
}


def current_doc_catalog() -> list[dict[str, str]]:
    settings = get_settings()
    return build_doc_catalog(
        settings.edu_doc_commitment_url,
        settings.edu_doc_training_url,
        settings.edu_doc_manual_url,
    )


@router.get("/docs")
def list_system_docs(current_user=Depends(get_current_user)):
    """登录用户均可查看系统文档目录。"""
    _ = current_user
    return success_response(data=current_doc_catalog())


@router.get("/docs/{doc_key}/file")
def stream_system_doc(doc_key: str):
    """转发原文。视频由浏览器直接下载，不要求登录头，避免整段读进页面。"""
    item = find_doc(current_doc_catalog(), doc_key)
    if item is None:
        raise NotFoundException("文档不存在")
    url = item["url"]
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BadRequestException("文档地址无效")
    request = urllib.request.Request(url, headers={"User-Agent": "awesome-data"})
    try:
        remote = urllib.request.urlopen(request, timeout=120)
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise NotFoundException("文档暂时无法打开，请稍后重试") from None

    def chunks():
        try:
            while True:
                block = remote.read(64 * 1024)
                if not block:
                    break
                yield block
        finally:
            remote.close()

    filename = quote(item["filename"])
    disposition = "attachment" if item["kind"] == "video" else "inline"
    return StreamingResponse(
        chunks(),
        media_type=_MEDIA.get(item["kind"], "application/octet-stream"),
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}"},
    )

from pathlib import Path

import pytest

from src.agent.education.paper_import import pdf_has_text_layer

_SCANNED = Path("temp/教科院/考试/2027届-高二/2027届高二6月期末/试卷/语文.pdf")
_TEXT = Path("temp/教科院/考试/2026届-高三/2026届高三1月期末/试卷/语文.pdf")


@pytest.mark.skipif(not _SCANNED.is_file(), reason="本地无文字层试卷不在")
def test_scanned_chinese_pdf_has_no_text_layer():
    assert pdf_has_text_layer(_SCANNED.read_bytes()) is False


@pytest.mark.skipif(not _TEXT.is_file(), reason="本地文字层试卷不在")
def test_text_chinese_pdf_has_text_layer():
    assert pdf_has_text_layer(_TEXT.read_bytes()) is True

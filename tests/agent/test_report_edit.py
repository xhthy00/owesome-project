"""report_edit 建议区提取/回写。"""

from src.agent.education.report_edit import (
    extract_recommendations_text,
    has_recommendations_section,
    replace_recommendations_html,
)


def test_marked_section_roundtrip():
    html = (
        '<h2>干预建议</h2>'
        '<div data-edu-section="recommendations"><p>旧建议A</p></div>'
    )
    assert extract_recommendations_text(html) == "旧建议A"
    updated = replace_recommendations_html(html, "新建议\n第二行")
    assert 'data-edu-section="recommendations"' in updated
    assert "新建议" in updated
    assert "旧建议A" not in updated
    assert extract_recommendations_text(updated) == "新建议\n第二行"


def test_heading_fallback_without_marker():
    html = "<h2>改进建议</h2><div><p>班级需加强训练</p></div>"
    assert has_recommendations_section(html)
    assert "加强训练" in (extract_recommendations_text(html) or "")
    updated = replace_recommendations_html(html, "已修订建议")
    assert "已修订建议" in updated

# -*- coding: utf-8 -*-
"""从 系统介绍与演示讲稿.html 生成 Word 版(重点加粗)。"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor, Cm

HTML = r"e:\work\code\awesome-data\docs\系统介绍与演示讲稿.html"
OUT = r"C:\Users\54567\Desktop\系统介绍与演示讲稿_v2.docx"

NAVY = RGBColor(0x1A, 0x3C, 0x6E)
BLUE = RGBColor(0x25, 0x60, 0xAB)
GOLD = RGBColor(0x8A, 0x5A, 0x00)
GREEN = RGBColor(0x2E, 0x7D, 0x32)
RED = RGBColor(0xC0, 0x39, 0x2B)
PURPLE = RGBColor(0x5E, 0x35, 0xB1)
GRAY = RGBColor(0x66, 0x66, 0x66)


def add_runs(par, html_text, base_bold=False, color=None, size=None):
    """把含 <b> 的片段写成 run,加粗重点。"""
    for seg in re.split(r"(<b>.*?</b>)", html_text):
        if not seg:
            continue
        if seg.startswith("<b>"):
            r = par.add_run(re.sub(r"</?b>", "", seg))
            r.bold = True
        else:
            r = par.add_run(seg)
            r.bold = base_bold
        if color is not None:
            r.font.color.rgb = color
        if size is not None:
            r.font.size = Pt(size)


def clean(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t).strip()


doc = Document()
# 页边距收窄
for s in doc.sections:
    s.left_margin = Cm(2.2)
    s.right_margin = Cm(2.2)

style = doc.styles["Normal"]
style.font.name = "微软雅黑"
style.font.size = Pt(11)

with open(HTML, encoding="utf-8") as f:
    html = f.read()

# 标题
t = doc.add_paragraph()
t.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = t.add_run("系统介绍与现场演示讲稿")
r.bold = True
r.font.size = Pt(20)
r.font.color.rgb = NAVY
sub = doc.add_paragraph()
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = sub.add_run("全程约 36 分钟 ｜ 🎙照念=台词 ｜ 🖱操作=你的动作 ｜ 💬=输入的问句 ｜ ⏳=等待期填词")
r.font.size = Pt(9)
r.font.color.rgb = GRAY

# 逐块解析 sec
blocks = re.findall(r'<div class="(?:part|sec)"[^>]*>(.*?)</div>\s*(?=<div class="(?:part|sec)"|<!-- =|<div style="height|$)', html, re.S)
# 更稳:按 part / sec 顺序切
tokens = re.split(r'(<div class="(?:part|sec)"[^>]*>|</div>\s*<!-- |(?=<!-- 演示))', html)

# 简化:手抓每个 sec 块(非嵌套结构,按 <div class="sec" 切)
sec_blocks = re.split(r'(?=<div class="(?:part|sec)")', html)
for block in sec_blocks[1:]:
    m_part = re.match(r'<div class="part"([^>]*)>(.*?)</div>', block, re.S)
    m_sec = re.match(r'<div class="sec"([^>]*)>(.*)', block, re.S)
    if m_part:
        pid = re.search(r'id="([^"]+)"', m_part.group(1) or "")
        title = clean(m_part.group(2))
        if not title or title in ("快速跳转",):
            continue
        p = doc.add_paragraph()
        p.space_before = Pt(14)
        hr = p.add_run("◆ " + title)
        hr.bold = True
        hr.font.size = Pt(15)
        hr.font.color.rgb = NAVY
        continue
    if not m_sec:
        continue
    body = m_sec.group(2)
    # 截到本 sec 结束(下一个 </div> 平衡处,非嵌套,取到末尾也行,后续解析按标签)
    h2 = re.search(r"<h2>(.*?)</h2>", body, re.S)
    h3s = re.split(r"(<h3>.*?</h3>)", body, flags=re.S)

    if h2:
        title_html = h2.group(1)
        dur = ""
        m_dur = re.search(r'<span class="dur">(.*?)</span>', title_html)
        if m_dur:
            dur = "（" + clean(m_dur.group(1)) + "）"
            title_html = title_html.replace(m_dur.group(0), "")
        p = doc.add_paragraph()
        r = p.add_run(clean(title_html) + dur)
        r.bold = True
        r.font.size = Pt(13.5)
        r.font.color.rgb = BLUE

    for chunk in h3s:
        m_h3 = re.match(r"<h3>(.*?)</h3>", chunk, re.S)
        if m_h3:
            p = doc.add_paragraph()
            r = p.add_run("▍" + clean(m_h3.group(1)))
            r.bold = True
            r.font.size = Pt(11.5)
            r.font.color.rgb = NAVY
            continue
        # 块级元素
        for m in re.finditer(
            r'<div class="(say|act|note|q|wait)">((?:(?!</div>).)*)</div>|<ul>(.*?)</ul>|<table>(.*?)</table>',
            chunk,
            re.S,
        ):
            kind = m.group(1)
            content = m.group(2) if m.group(1) else ""
            if kind == "say":
                p = doc.add_paragraph()
                tag = p.add_run("【念】")
                tag.bold = True
                tag.font.color.rgb = GOLD
                tag.font.size = Pt(10)
                add_runs(p, content.strip(), color=GOLD)
            elif kind == "act":
                p = doc.add_paragraph()
                tag = p.add_run("【操作】")
                tag.bold = True
                tag.font.color.rgb = GREEN
                tag.font.size = Pt(10)
                add_runs(p, content.strip(), color=GREEN)
            elif kind == "q":
                p = doc.add_paragraph()
                tag = p.add_run("【输入】")
                tag.bold = True
                tag.font.color.rgb = BLUE
                tag.font.size = Pt(10)
                r = p.add_run(clean(content))
                r.bold = True
                r.font.color.rgb = BLUE
            elif kind == "wait":
                p = doc.add_paragraph()
                tag = p.add_run("【等待期念】")
                tag.bold = True
                tag.font.color.rgb = PURPLE
                tag.font.size = Pt(10)
                add_runs(p, content.strip(), color=PURPLE)
            elif kind == "note":
                p = doc.add_paragraph()
                tag = p.add_run("【注意】")
                tag.bold = True
                tag.font.color.rgb = RED
                tag.font.size = Pt(10)
                add_runs(p, content.strip(), color=RED)
            elif m.group(3) is not None:
                for li in re.findall(r"<li>(.*?)</li>", m.group(3), re.S):
                    p = doc.add_paragraph(style="List Bullet")
                    add_runs(p, li.strip())
            elif m.group(4) is not None:
                tbl_body = m.group(4)
                rows = re.findall(r"<tr>(.*?)</tr>", tbl_body, re.S)
                parsed = []
                for row in rows:
                    cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)
                    parsed.append(cells)
                if parsed:
                    table = doc.add_table(rows=len(parsed), cols=len(parsed[0]))
                    table.style = "Table Grid"
                    for i, row in enumerate(parsed):
                        for j, cell in enumerate(row):
                            if j < len(table.rows[i].cells):
                                c = table.rows[i].cells[j]
                                c.text = ""
                                add_runs(c.paragraphs[0], clean(cell), base_bold=(i == 0))
                doc.add_paragraph()

doc.save(OUT)
print("saved:", OUT)

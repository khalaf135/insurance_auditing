"""Render the two Markdown deliverables. Optional dependency: docs/requirements.txt.

Run from anywhere: python3 docs/render_reports.py
Outputs are written under output/pdf; source Markdown remains editable.
"""
from html import escape
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/pdf'
INK, ACCENT = colors.HexColor('#182633'), colors.HexColor('#225B68')


def inline(text):
    text = escape(text)
    text = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    return re.sub(r'`([^`]+)`', r'<font name="Courier">\1</font>', text)


def render(source, destination, compact=False):
    styles = {
        'body': ParagraphStyle('body', fontName='Helvetica', fontSize=9 if compact else 9.5,
                               leading=11.8 if compact else 12.6, textColor=INK, spaceAfter=6),
        'h1': ParagraphStyle('h1', fontName='Helvetica-Bold', fontSize=21, leading=25,
                            textColor=INK, spaceAfter=10),
        'h2': ParagraphStyle('h2', fontName='Helvetica-Bold', fontSize=10.5, leading=14,
                            textColor=ACCENT, spaceBefore=7, spaceAfter=5, keepWithNext=True),
        'cell': ParagraphStyle('cell', fontName='Helvetica', fontSize=8.2, leading=10, textColor=INK),
    }
    document = SimpleDocTemplate(str(destination), pagesize=A4,
        leftMargin=40, rightMargin=40, topMargin=32, bottomMargin=34,
        title=source.stem.replace('_', ' ').title(), author='Prepared with Codex assistance')
    lines = source.read_text(encoding='utf-8').splitlines()
    story, paragraph = [], []

    def flush():
        if paragraph:
            story.append(Paragraph(inline(' '.join(paragraph)), styles['body']))
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('|'):
            flush()
            table_rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [x.strip() for x in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', x) for x in cells):
                    table_rows.append([Paragraph(inline(x), styles['cell']) for x in cells])
                i += 1
            table = Table(table_rows, colWidths=[250, 34, 34, 34, 82, 81], repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E9F0F1')),
                ('LINEBELOW', (0, 0), (-1, 0), .6, ACCENT),
                ('LINEBELOW', (0, 1), (-1, -1), .2, colors.HexColor('#D8E0E3')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.extend([table, Spacer(1, 6)])
            continue
        if not line:
            flush()
        elif line.startswith('# '):
            flush()
            story.append(Paragraph(inline(line[2:]), styles['h1']))
        elif line.startswith('## '):
            flush()
            if source.name == 'EVALUATION.md' and line == '## Four ways the approach still falls short':
                story.append(PageBreak())
            story.append(Paragraph(inline(line[3:]), styles['h2']))
        elif re.match(r'\d+\. ', line) and paragraph:
            flush()
            paragraph.append(line)
        else:
            paragraph.append(line)
        i += 1
    flush()

    def footer(canvas, doc):
        canvas.setStrokeColor(colors.HexColor('#D8E0E3'))
        canvas.line(40, 27, A4[0]-40, 27)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(INK)
        canvas.drawString(40, 16, 'Insurance auditing | Development evidence, not held-out validation')
        canvas.drawRightString(A4[0]-40, 16, str(doc.page))

    document.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    render(ROOT / 'docs/EVALUATION.md', OUT / 'evaluation_report.pdf')
    render(ROOT / 'docs/DECISION_LOG.md', OUT / 'decision_log.pdf', compact=True)
    print(OUT)

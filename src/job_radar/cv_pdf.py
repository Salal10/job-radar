"""Render a CV written in a small Markdown format to a clean, ATS-friendly PDF (one column, real text).

  uv run python -m job_radar.cv_pdf applications/acme-backend/cv.md      -> applications/acme-backend/cv.pdf

The format (see cvs/master.md for a full example):
  # FULL NAME
  > Headline | Skill · Skill
  Contact line (city | phone | email)            any plain lines before the first "##"
  ## SECTION TITLE                                 optional " — note" after the title is shown in grey
  ### Company or project | Right side             right side: location (or tech stack for projects)
  *Job title* | Jan 2024 – Present                 italic line with dates on the right
  - Bullet with **bold** and *italic*
  Plain paragraph text

It tries to fit on one page by stepping the font size down a little; if it can't, it uses two pages.
"""
import io
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY, BLUE, GREY, TEXT = colors.HexColor("#1F3A5F"), colors.HexColor("#2F5D9A"), colors.HexColor("#555555"), colors.HexColor("#1E1E1E")
LINK = re.compile(r"([\w.+-]+@[\w-]+\.[\w.]+|(?:https?://)?(?:www\.)?[\w-]+\.(?:com|app|io|dev|net|org|me)(?:/[\w./-]*)?)")


def register_fonts() -> str:
    """Georgia (Windows) looks closest to the original CVs; fall back to the built-in Times."""
    fonts = Path("C:/Windows/Fonts")
    files = {"Serif": "georgia.ttf", "Serif-Bold": "georgiab.ttf", "Serif-Italic": "georgiai.ttf", "Serif-BoldItalic": "georgiaz.ttf"}
    if all((fonts / f).exists() for f in files.values()):
        for name, f in files.items():
            pdfmetrics.registerFont(TTFont(name, str(fonts / f)))
        pdfmetrics.registerFontFamily("Serif", normal="Serif", bold="Serif-Bold", italic="Serif-Italic",
                                      boldItalic="Serif-BoldItalic")
        return "Serif"
    return "Times-Roman"


def inline(text: str, links: bool = False) -> str:
    """Markdown bold/italic -> reportlab markup; escape the rest."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    if links:
        def link(m):
            target = m.group(1)
            href = f"mailto:{target}" if "@" in target else (target if target.startswith("http") else f"https://{target}")
            return f'<link href="{href}"><font color="{BLUE.hexval()}">{target}</font></link>'
        text = LINK.sub(link, text)
    return text


def styles(font: str, size: float) -> dict[str, ParagraphStyle]:
    lead = size * 1.24
    base = ParagraphStyle("base", fontName=font, fontSize=size, leading=lead, textColor=TEXT)
    return {
        "name": ParagraphStyle("name", base, fontName=f"{font}-Bold" if font == "Serif" else "Times-Bold",
                               fontSize=size * 2.3, leading=size * 2.7, alignment=TA_CENTER, textColor=NAVY),
        "headline": ParagraphStyle("headline", base, fontSize=size * 1.15, leading=size * 1.5, alignment=TA_CENTER,
                                   textColor=BLUE),
        "contact": ParagraphStyle("contact", base, fontSize=size * 0.95, leading=size * 1.25, alignment=TA_CENTER,
                                  textColor=GREY),
        "section": ParagraphStyle("section", base, fontSize=size * 1.1, leading=size * 1.35, textColor=BLUE,
                                  spaceBefore=size * 0.55),
        "body": base,
        "left": ParagraphStyle("left", base, textColor=NAVY),
        "right": ParagraphStyle("right", base, alignment=TA_RIGHT, textColor=GREY),
        "bullet": ParagraphStyle("bullet", base, leading=lead * 0.98),
    }


def two_sides(left: str, right: str, s: dict, width: float, left_style: str = "left") -> Table:
    table = Table([[Paragraph(left, s[left_style]), Paragraph(right, s["right"])]], colWidths=[width * 0.62, width * 0.38])
    table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return table


def story(lines: list[str], s: dict, width: float) -> list:
    out, bullets, in_header = [], [], True

    def flush():
        if bullets:
            out.append(ListFlowable([ListItem(Paragraph(b, s["bullet"]), leftIndent=12, value="•") for b in bullets],
                                    bulletType="bullet", start="•", leftIndent=12, bulletFontSize=s["body"].fontSize * 0.8))
            bullets.clear()

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("- "):
            bullets.append(inline(line[2:]))
            continue
        flush()
        if line.startswith("# "):
            out.append(Paragraph(inline(line[2:]), s["name"]))
        elif line.startswith("> "):
            out.append(Paragraph(f"<b>{inline(line[2:])}</b>", s["headline"]))
        elif line.startswith("## "):
            in_header = False
            title, _, note = line[3:].partition(" — ")
            text = f"<b>{inline(title)}</b>" + (f' <font color="{GREY.hexval()}" size="{s["body"].fontSize}">— {inline(note)}</font>' if note else "")
            out.append(Paragraph(text, s["section"]))
            out.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#9DB3D3"), spaceBefore=1, spaceAfter=3))
        elif line.startswith("### "):
            left, _, right = line[4:].partition(" | ")
            italic_right = not re.search(r"[A-Z][a-z]+, [A-Z]", right)  # a location is bold, a tech stack italic
            right_html = f"<i>{inline(right)}</i>" if italic_right else f"<b>{inline(right)}</b>"
            out.append(Spacer(1, 2))
            out.append(two_sides(f"<b>{inline(left)}</b>", right_html, s, width))
        elif re.match(r"^\*[^*].*\*( \| .*)?$", line):
            left, _, right = line.partition(" | ")
            out.append(two_sides(inline(left), f"<i>{inline(right)}</i>", s, width, left_style="body"))
        elif in_header:
            out.append(Paragraph(inline(line, links=True), s["contact"]))
        else:
            out.append(Paragraph(inline(line), s["body"]))
    flush()
    return out


def render(markdown: str, size: float, font: str) -> tuple[bytes, int]:
    buffer = io.BytesIO()
    margin = 0.5 * inch
    doc = SimpleDocTemplate(buffer, pagesize=LETTER, leftMargin=margin, rightMargin=margin, topMargin=0.4 * inch,
                            bottomMargin=0.4 * inch, title="CV", author=markdown.splitlines()[0].lstrip("# ").strip())
    doc.build(story(markdown.splitlines(), styles(font, size), doc.width - 12))  # the frame pads 6 pt each side
    return buffer.getvalue(), doc.page


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    source = Path(sys.argv[1])
    markdown = source.read_text(encoding="utf-8")
    font = register_fonts()
    for size in (9.6, 9.3, 9.0, 8.7):  # small steps only: below ~8.7 pt a CV gets hard to read
        pdf, pages = render(markdown, size, font)
        if pages == 1:
            break
    out = source.with_suffix(".pdf")
    out.write_bytes(pdf)
    print(f"{out}  ({pages} page{'s' if pages > 1 else ''}, {size} pt)")


if __name__ == "__main__":
    main()

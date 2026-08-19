"""
nodes/report.py — Generate the local HTML review page.
Professor cards: mailto button. Job cards: application link + why-this-role text.
"""

import os
import urllib.parse
from datetime import datetime

from config import OUTPUT_DIR


def _badge(text: str, colour: str) -> str:
    return f'<span style="background:{colour};color:#fff;padding:3px 9px;border-radius:4px;font-size:12px;font-weight:600">{text}</span>'


def _pdf_line(cv_pdf_path: str) -> str:
    """Surfaces whether this candidate got the tailored CV or the untouched
    base-CV fallback (ReAct loop exhausted/validation failed) — visible
    right in the report rather than only in the log file."""
    if not cv_pdf_path:
        return "⚠️ CV compilation failed — attach base CV manually."
    abs_pdf = os.path.abspath(cv_pdf_path).replace("\\", "/")
    if cv_pdf_path.endswith("_base.pdf"):
        note = _badge("base CV (tailoring unavailable)", "#b45309")
    else:
        note = _badge("tailored", "#1a7f37")
    return f'📎 <code style="word-break:break-all;font-size:12px">{abs_pdf}</code> {note}'


def _score_bar(score: float) -> str:
    pct = min(100, int((score or 0) * 10))
    colour = "#1a7f37" if pct >= 70 else "#b45309" if pct >= 40 else "#b91c1c"
    return (
        f'<div style="display:flex;align-items:center;gap:8px">'
        f'<div style="width:120px;height:8px;background:#e5e7eb;border-radius:4px;overflow:hidden">'
        f'<div style="width:{pct}%;height:100%;background:{colour};border-radius:4px"></div></div>'
        f'<span style="font-size:13px;color:#555">{score:.1f}/10</span></div>'
    )


def _prof_card(idx: int, c: dict) -> str:
    to_field = c.get("email") or f"{c['name']} <fill_email_here>"
    conf_colours = {"verified": "#1a7f37", "inferred": "#b45309", "missing": "#b91c1c"}
    conf_labels = {"verified": "✓ verified", "inferred": "~ inferred", "missing": "✗ missing"}
    conf = c.get("email_conf", "missing")

    body = c.get("email_body", "")[:1800]
    mailto = f"mailto:?" + urllib.parse.urlencode(
        {"to": to_field, "subject": c.get("email_subject", ""), "body": body}
    )

    pdf_line = _pdf_line(c.get("cv_pdf_path", ""))

    date_badge = _badge("🎯 early start-date match", "#7c3aed") if c.get("start_date_match") else ""

    return f"""
<div style="border:1px solid #e5e7eb;border-radius:10px;padding:24px;margin-bottom:28px;background:#fff">
  <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">
    <div>
      <h2 style="margin:0 0 4px;font-size:19px">{idx}. {c['name']}</h2>
      <div style="color:#555;font-size:14px">{c['org']} &nbsp;·&nbsp; via {c['source']}</div>
      {f'<a href="{c["url"]}" target="_blank" style="font-size:13px;color:#2563eb">View profile →</a>' if c.get('url') else ''}
    </div>
    <div style="text-align:right">
      {_score_bar(c.get('score', 0))}
      <div style="margin-top:6px">{_badge(conf_labels.get(conf,conf), conf_colours.get(conf,'#555'))} {date_badge}</div>
    </div>
  </div>
  <div style="margin-top:8px;font-size:13px;color:#444;font-style:italic">💡 {c.get('reason','')}</div>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0">
  <label style="font-size:12px;font-weight:600;color:#374151">TO:</label>
  <input type="text" value="{to_field}" id="to-{idx}" style="width:100%;padding:8px;border:1px solid #d1d5db;border-radius:6px;margin:4px 0 10px;box-sizing:border-box">
  <label style="font-size:12px;font-weight:600;color:#374151">SUBJECT:</label>
  <input type="text" value="{c.get('email_subject','')}" id="subj-{idx}" style="width:100%;padding:8px;border:1px solid #d1d5db;border-radius:6px;margin:4px 0 10px;box-sizing:border-box">
  <label style="font-size:12px;font-weight:600;color:#374151">BODY:</label>
  <textarea rows="12" id="body-{idx}" style="width:100%;padding:10px;border:1px solid #d1d5db;border-radius:6px;font-family:inherit;line-height:1.6;box-sizing:border-box">{c.get('email_body','')}</textarea>
  <div style="margin-top:10px;padding:10px;background:#f0fdf4;border-radius:6px;font-size:13px">{pdf_line}</div>
  <div style="margin-top:14px">
    <a href="{mailto}" onclick="updateMailto(event,{idx})" style="background:#2563eb;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600;font-size:14px">✉️ Open in Gmail</a>
  </div>
</div>"""


def _job_card(idx: int, c: dict) -> str:
    date_badge = _badge("🎯 early start-date match", "#7c3aed") if c.get("start_date_match") else ""
    pdf_line = _pdf_line(c.get("cv_pdf_path", ""))

    return f"""
<div style="border:1px solid #e5e7eb;border-radius:10px;padding:24px;margin-bottom:28px;background:#fff">
  <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">
    <div>
      <h2 style="margin:0 0 4px;font-size:19px">{idx}. {c['name']}</h2>
      <div style="color:#555;font-size:14px">{c['org']} &nbsp;·&nbsp; {c.get('location','')} &nbsp;·&nbsp; via {c['source']}</div>
      {f'<a href="{c["url"]}" target="_blank" style="font-size:13px;color:#2563eb">Open application page →</a>' if c.get('url') else ''}
    </div>
    <div style="text-align:right">
      {_score_bar(c.get('score', 0))}
      <div style="margin-top:6px">{date_badge}</div>
    </div>
  </div>
  <div style="margin-top:8px;font-size:13px;color:#444;font-style:italic">💡 {c.get('reason','')}</div>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0">
  <label style="font-size:12px;font-weight:600;color:#374151">WHY THIS ROLE (paste into application):</label>
  <textarea rows="6" style="width:100%;padding:10px;border:1px solid #d1d5db;border-radius:6px;font-family:inherit;line-height:1.6;box-sizing:border-box;margin-top:4px">{c.get('why_this_role','')}</textarea>
  <div style="margin-top:10px;padding:10px;background:#f0fdf4;border-radius:6px;font-size:13px">{pdf_line}</div>
</div>"""


def generate_report(state: dict) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUTPUT_DIR, f"drafts_{timestamp}.html")

    finished = state["finished"]
    cards = ""
    for i, c in enumerate(finished, 1):
        cards += _prof_card(i, c) if c["kind"] == "professor" else _job_card(i, c)

    stop_note = f"<p style='color:#6b7280;font-size:13px'>Search stopped: {state.get('stop_reason','')}</p>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Outreach Drafts — {timestamp}</title>
<style>body{{font-family:-apple-system,'Segoe UI',sans-serif;background:#f9fafb;color:#111;margin:0;padding:24px}}
.container{{max-width:860px;margin:0 auto}} h1{{font-size:24px;margin-bottom:4px}}
.meta{{color:#6b7280;font-size:14px;margin-bottom:20px}}</style></head>
<body><div class="container">
<h1>📬 {len(finished)} Drafts Ready</h1>
<div class="meta">Generated {datetime.now().strftime("%d %b %Y, %H:%M")}</div>
{stop_note}
{cards}
</div>
<script>
function updateMailto(e, idx) {{
  e.preventDefault();
  const to = document.getElementById('to-'+idx).value;
  const subject = document.getElementById('subj-'+idx).value;
  const body = document.getElementById('body-'+idx).value.substring(0,1800);
  window.location.href = 'mailto:?' + new URLSearchParams({{to,subject,body}}).toString();
}}
</script></body></html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path

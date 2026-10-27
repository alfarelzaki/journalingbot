"""Organized export, generated from the database (the source of truth).

Layout:
    README.md                      index
    daily/YYYY/MM/YYYY-MM-DD.md    one file per day (YAML front-matter)
    weekly/YYYY-Www.md
    monthly/YYYY-MM.md
    data/entries.json              full structured data
    data/summaries.csv             spreadsheet-friendly

Raw journal text is excluded unless explicitly requested: export zips get
forwarded, synced to cloud drives and opened on shared computers.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date

from journalbot.db import Store
from journalbot.formatting import MdFmt, render_daily
from journalbot.recaps import render_monthly, render_weekly


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet formula injection: a cell starting with = + - @
    would be executed by Excel/Sheets."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def build_export(store: Store, user_id: int, include_raw: bool = False, month: str | None = None) -> bytes:
    fmt = MdFmt()
    files: dict[str, str] = {}
    entries_json: list[dict] = []
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["date", "grateful", "mistakes", "learned"])

    daily_links: list[str] = []
    for entry in store.entries_between(user_id, date(1970, 1, 1), date(9999, 12, 31)):
        if month and not entry.local_date.startswith(month):
            continue
        summary = store.get_summary(entry.id)
        msgs = store.messages(entry.id)
        record: dict = {"date": entry.local_date, "state": entry.state, "summary_status": entry.summary_status}
        if summary:
            record["summary"] = {k: getattr(summary, k) for k in ("grateful", "mistakes", "learned", "language")}
            writer.writerow(
                [entry.local_date] + [_csv_safe(" | ".join(getattr(summary, k))) for k in ("grateful", "mistakes", "learned")]
            )
        clarifications = [t for role, t in store.refinements(entry.id) if role == "user"]
        if include_raw:
            record["messages"] = [{"source": m.source, "text": m.text, "sent_at": m.sent_at.isoformat()} for m in msgs]
            record["clarifications"] = clarifications
        entries_json.append(record)
        if not summary and not (include_raw and msgs):
            continue

        d = date.fromisoformat(entry.local_date)
        sources = {m.source for m in msgs}
        words = sum(len(m.text.split()) for m in msgs)
        front = [
            "---",
            f"date: {entry.local_date}",
            f"weekday: {d.strftime('%A')}",
            f"word_count: {words}",
            f"source: {'mixed' if len(sources) > 1 else (next(iter(sources)) if sources else 'none')}",
            "---",
            "",
        ]
        body = "\n\n".join(render_daily(fmt, d, summary)) if summary else "# " + entry.local_date + "\n\n*(not summarized)*"
        if include_raw and msgs:
            body += "\n\n## Raw notes\n\n" + "\n\n".join(m.text for m in msgs)
            if clarifications:
                body += "\n\n## Clarifications\n\n" + "\n".join(f"- {c}" for c in clarifications)
        path = f"daily/{d.year:04d}/{d.month:02d}/{entry.local_date}.md"
        files[path] = "\n".join(front) + body + "\n"
        daily_links.append(f"- [{entry.local_date}]({path})")

    weekly_links: list[str] = []
    for w in store.list_weekly(user_id):
        if w["status"] not in ("ok", "stale"):
            continue
        monday = date.fromisoformat(w["key"])
        if month and not (w["key"].startswith(month)):
            continue
        iso = monday.isocalendar()
        path = f"weekly/{iso.year}-W{iso.week:02d}.md"
        head = f"---\nweek_start: {w['key']}\nstatus: {w['status']}\n---\n"
        files[path] = head + "\n\n".join(render_weekly(fmt, monday, w["content"])) + "\n"
        weekly_links.append(f"- [{path[7:-3]}]({path})")

    monthly_links: list[str] = []
    for m in store.list_monthly(user_id):
        if m["status"] not in ("ok", "stale") or (month and m["key"] != month):
            continue
        path = f"monthly/{m['key']}.md"
        head = f"---\nmonth: {m['key']}\nstatus: {m['status']}\n---\n"
        files[path] = head + "\n\n".join(render_monthly(fmt, m["key"], m["content"])) + "\n"
        monthly_links.append(f"- [{m['key']}]({path})")

    files["data/entries.json"] = json.dumps(entries_json, ensure_ascii=False, indent=2)
    files["data/summaries.csv"] = csv_buf.getvalue()
    scope = f" ({month})" if month else ""
    files["README.md"] = "\n".join(
        [f"# Journal export{scope}", "", f"Raw notes included: {'yes' if include_raw else 'no'}", ""]
        + ["## Monthly", *monthly_links, "", "## Weekly", *weekly_links, "", "## Daily", *daily_links, ""]
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in sorted(files.items()):
            zf.writestr(f"journal-export/{path}", content)
    return buf.getvalue()

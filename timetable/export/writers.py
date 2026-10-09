"""Exports: long-format Excel workbook, CSV and a self-contained HTML viewer that renders
per-group / per-room timetables in the same layout as the university's printed schedule."""
from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path

from ..config import DAYS
from ..domain.models import Session

KIND_LABEL = {"lecture": "lecture", "practice": "practice", "lab": "lab"}


def rows(sessions: list[Session], starts: dict[str, int], rooms_of: dict[str, str], labels: dict[int, str],
         H: int = 12) -> list[dict]:
    out = []
    for s in sessions:
        if s.id not in starts:
            continue
        t0 = starts[s.id]
        for k in range(s.duration):
            d, h = divmod(t0 + k, H)
            for g in s.groups:
                out.append({"group": g, "program": s.program, "year": s.year, "day": DAYS[d], "day_index": d,
                            "hour_index": h, "time": labels.get(h, f"{8 + h:02d}:00"), "discipline": s.course,
                            "type": KIND_LABEL[s.kind],
                            "classroom": "online" if s.online else rooms_of.get(s.id, "UNASSIGNED"),
                            "stream_size": len(s.groups)})
    out.sort(key=lambda r: (r["group"], r["day_index"], r["hour_index"]))
    return out


def write_csv(path: Path, data: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(data[0]) if data else ["group"])
        w.writeheader()
        w.writerows(data)
    return path


def write_excel(path: Path, data: list[dict], issues: list[dict], load: dict, metrics: dict) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Timetable"
    cols = ["group", "day", "time", "discipline", "classroom", "type", "program", "year", "stream_size"]
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDDDDD")
    for r in data:
        ws.append([r[c] for c in cols])
    ws.freeze_panes = "A2"
    for col, width in zip("ABCDEFGHI", [12, 6, 13, 55, 14, 10, 8, 6, 8]):
        ws.column_dimensions[col].width = width

    rs = wb.create_sheet("Rooms")
    rs.append(["room", "booked_hours_per_week"])
    for room, n in sorted(Counter(r["classroom"] for r in data if r["classroom"] not in ("online",)).items()):
        rs.append([room, n])
    iss = wb.create_sheet("Data issues")
    iss.append(["level", "where", "issue"])
    for i in issues:
        iss.append([i["level"], i["where"], i["issue"]])
    ld = wb.create_sheet("Agent load")
    ld.append(["agent", "calls", "share"])
    for a, v in load["agents"].items():
        ld.append([a, v["calls"], v["share"]])
    mt = wb.create_sheet("Quality")
    for k, v in metrics.items():
        mt.append([k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v])
    wb.save(path)
    return path


def write_html(path: Path, data: list[dict], labels: dict[int, str], title: str, summary: dict) -> Path:
    payload = json.dumps([[r["group"], r["day_index"], r["hour_index"], r["discipline"], r["classroom"], r["type"]]
                          for r in data], ensure_ascii=False)
    times = json.dumps([labels.get(h, f"{8 + h:02d}:00") for h in range(12)])
    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root{{--bg:#fff;--fg:#111;--muted:#666;--line:#bbb;--head:#e6e6e6;--lec:#e8f0ff;--prac:#fff;--lab:#eefbea;--on:#fff7e0}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#16181c;--fg:#eee;--muted:#999;--line:#444;--head:#2a2d33;--lec:#1d2a44;--prac:#16181c;--lab:#1c3322;--on:#3a321a}}}}
body{{font:14px/1.4 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:16px}}
h1{{font-size:18px;margin:0 0 4px}} .muted{{color:var(--muted)}}
select,input{{font:inherit;padding:4px 6px;background:var(--bg);color:var(--fg);border:1px solid var(--line)}}
table{{border-collapse:collapse;width:100%;margin-top:12px}} th,td{{border:1px solid var(--line);padding:3px 6px;vertical-align:top}}
th{{background:var(--head);text-align:left}} td.day{{font-size:16px;text-align:center;vertical-align:middle;width:110px}}
tr.lecture td{{background:var(--lec)}} tr.lab td{{background:var(--lab)}} tr.online td{{background:var(--on)}}
.wrap{{overflow-x:auto}}
</style></head><body>
<h1>{html.escape(title)}</h1><div class="muted">{html.escape(json.dumps(summary, ensure_ascii=False))}</div>
<p>Группа: <select id="g"></select> &nbsp; или аудитория: <select id="r"><option value="">—</option></select></p>
<div class="wrap"><table id="t"></table></div>
<script>
const D={payload};const T={times};const DAYS=["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
const g=document.getElementById('g'),r=document.getElementById('r'),t=document.getElementById('t');
[...new Set(D.map(x=>x[0]))].sort().forEach(v=>g.add(new Option(v,v)));
[...new Set(D.map(x=>x[4]).filter(v=>v!=='online'))].sort().forEach(v=>r.add(new Option(v,v)));
function esc(s){{return String(s).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]))}}
function render(rows,who){{rows.sort((a,b)=>a[1]-b[1]||a[2]-b[2]);
 let h='<tr><th>Day of the week</th><th>Time</th><th>Discipline</th><th>'+(who==='room'?'Group':'Classroom')+'</th><th>Type</th></tr>';
 const byDay={{}};rows.forEach(x=>(byDay[x[1]]=byDay[x[1]]||[]).push(x));
 Object.keys(byDay).forEach(d=>{{byDay[d].forEach((x,i)=>{{const cls=x[4]==='online'?'online':x[5];
  h+='<tr class="'+cls+'">'+(i===0?'<td class="day" rowspan="'+byDay[d].length+'">'+DAYS[d]+'</td>':'')+
  '<td>'+T[x[2]]+'</td><td>'+esc(x[3])+'</td><td>'+esc(who==='room'?x[0]:x[4])+'</td><td>'+x[5]+'</td></tr>'}})}});
 t.innerHTML=h;}}
function show(){{ if(r.value){{const m={{}};D.filter(x=>x[4]===r.value).forEach(x=>m[x[1]+'_'+x[2]+x[3]]=x);render(Object.values(m),'room')}}
 else render(D.filter(x=>x[0]===g.value),'group')}}
g.onchange=()=>{{r.value='';show()}};r.onchange=show;show();
</script></body></html>"""
    path.write_text(doc, encoding="utf-8")
    return path

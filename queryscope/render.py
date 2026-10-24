"""Render a QueryPlan as a self-contained HTML page.

The page shows the query in written order on top, then animates it in
logical execution order: press play and each clause lights up in the SQL
and in the flow diagram while a plain-English note explains what the
engine is doing at that step. No external assets, no dependencies.
"""

from __future__ import annotations

import html
import json

from queryscope.parser import QueryPlan

_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       margin: 0; padding: 28px 18px 48px; background: #0f1420; color: #e8ecf4; }
.wrap { max-width: 880px; margin: 0 auto; }
h1 { font-size: 26px; margin: 0; }
.tag { color: #9aa7bd; font-size: 14px; margin-top: 4px; }
.sql { margin-top: 18px; background: #161d2e; border: 1px solid #2a3550;
       border-radius: 10px; padding: 14px 16px; font-family: ui-monospace,
       SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px;
       line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
.sql span { border-radius: 4px; padding: 1px 3px; transition: background .3s, color .3s; }
.sql span.on { background: #f5b83d; color: #14100a; }
button#play { background: #f5b83d; color: #14100a; border: none; border-radius: 8px;
       padding: 9px 18px; font-size: 14px; font-weight: 700; cursor: pointer; }
button#play:hover { filter: brightness(1.08); }
.flow { display: flex; flex-wrap: wrap; align-items: stretch; gap: 6px; margin-top: 16px; }
.arrow { align-self: center; color: #9aa7bd; font-size: 15px; }
.step { flex: 1 1 120px; text-align: left; background: #161d2e; border: 1px solid #2a3550;
       border-radius: 10px; padding: 9px 11px; cursor: pointer; color: #e8ecf4;
       transition: border-color .3s, opacity .3s, box-shadow .3s; }
.step .n { font-size: 10px; color: #9aa7bd; letter-spacing: .06em; }
.step .t { font-size: 13px; font-weight: 700; margin-top: 2px; }
.step.on { border-color: #f5b83d; box-shadow: 0 0 0 1px #f5b83d; }
.step.done { opacity: .55; }
.note { margin-top: 16px; border-left: 3px solid #f5b83d; background: #161d2e;
       border-radius: 0 8px 8px 0; padding: 10px 14px; font-size: 14.5px; line-height: 1.55; }
.findings { margin-top: 22px; }
.findings h2 { font-size: 16px; margin: 0 0 8px; }
.finding { border: 1px solid #2a3550; border-radius: 10px; padding: 10px 14px;
       margin-bottom: 8px; background: #161d2e; font-size: 14px; line-height: 1.55; }
.finding .sev { font-size: 10px; font-weight: 700; letter-spacing: .07em;
       padding: 2px 7px; border-radius: 20px; margin-right: 8px; vertical-align: 2px; }
.sev-warn { background: #f5b83d; color: #14100a; }
.sev-info { background: #33415e; color: #cfd9ec; }
.finding b { font-size: 14px; }
.none { color: #9aa7bd; font-size: 14px; }
.foot { margin-top: 26px; color: #9aa7bd; font-size: 12.5px; line-height: 1.6; }
"""

_JS = """
(function(){
  var steps = JSON.parse(document.getElementById('qs-data').textContent);
  var boxes = Array.prototype.slice.call(document.querySelectorAll('.step'));
  var spans = Array.prototype.slice.call(document.querySelectorAll('[data-step]'));
  var note = document.getElementById('note');
  function show(i){
    boxes.forEach(function(b,j){
      b.classList.toggle('on', j===i);
      b.classList.toggle('done', j<i);
    });
    spans.forEach(function(sp){
      sp.classList.toggle('on', parseInt(sp.getAttribute('data-step'),10)===i);
    });
    note.textContent = steps[i].note;
  }
  boxes.forEach(function(b,i){ b.addEventListener('click', function(){ show(i); }); });
  var timer=null, cur=-1, btn=document.getElementById('play');
  btn.addEventListener('click', function(){
    if(timer){ clearInterval(timer); timer=null; btn.textContent='Play execution'; return; }
    cur=-1; btn.textContent='Stop';
    timer=setInterval(function(){
      cur++;
      if(cur>=steps.length){ clearInterval(timer); timer=null; btn.textContent='Play again'; return; }
      show(cur);
    }, 2100);
    cur=0; show(0);
  });
})();
"""


def render_html(plan: QueryPlan, title: str = "QueryScope") -> str:
    sql_lines = "\n".join(
        f'<span data-step="{idx}">{html.escape(text)}</span>'
        for idx, text in plan.written_lines)

    flow_parts: list[str] = []
    for i, step in enumerate(plan.steps):
        if i > 0:
            flow_parts.append('<div class="arrow">&rarr;</div>')
        flow_parts.append(
            f'<button class="step"><div class="n">STEP {i + 1} &middot; '
            f'{html.escape(step.kind)}</div><div class="t">'
            f'{html.escape(step.title)}</div></button>')
    flow = "".join(flow_parts)

    if plan.findings:
        findings = "".join(
            f'<div class="finding"><span class="sev sev-{f.severity}">'
            f'{f.severity.upper()}</span><b>{html.escape(f.title)}</b>'
            f'<div>{html.escape(f.detail)}</div></div>'
            for f in plan.findings)
    else:
        findings = '<div class="none">No expensive patterns spotted.</div>'

    steps_json = json.dumps(
        [{"note": s.note} for s in plan.steps]).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — watch your SQL actually run</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>{html.escape(title)}</h1>
  <div class="tag">Your query, replayed in the order the engine actually executes it.</div>
  <div class="sql">{sql_lines}</div>
  <div style="margin-top:16px;"><button id="play">Play execution</button></div>
  <div class="flow">{flow}</div>
  <div class="note" id="note">Press play. The engine starts at FROM, not at SELECT —
  that gap between written order and real order is the whole point.</div>
  <div class="findings"><h2>What it catches</h2>{findings}</div>
  <div class="foot">Logical execution order, as taught by every SQL engine's
  documentation. QueryScope reads the query structure only — it does not
  connect to your database, and row counts depend on your data.</div>
</div>
<script type="application/json" id="qs-data">{steps_json}</script>
<script>{_JS}</script>
</body>
</html>
"""

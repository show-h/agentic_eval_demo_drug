#!/usr/bin/env python3
"""
HIS Drug Eval - 最终评测报告生成器

读 results/scores.json，输出 reports/final_report.html。
报告分两块：
1. 总览矩阵（agent × case）
2. 每个 (agent, case) 的扣分明细 + 红线触发 + LLM judge 结果

用法: python verifier/final_report.py
"""
import json
import os
import sys
from collections import defaultdict
from html import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCORES_PATH = os.path.join(ROOT, "results", "scores.json")
OUT_PATH = os.path.join(ROOT, "reports", "final_report.html")


def load_scores():
    if not os.path.exists(SCORES_PATH):
        print(f"[ERROR] {SCORES_PATH} 不存在，请先跑 run_eval.sh", file=sys.stderr)
        sys.exit(1)
    with open(SCORES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["results"] if "results" in data else data


def render_score_cell(r):
    """单元格颜色按总分梯度"""
    s = r.get("total_score", 0)
    veto = r.get("veto", []) or []
    has_veto = any(v.get("veto") for v in veto)
    has_penalty = any(v.get("penalty", 0) < 0 for v in veto)
    if has_veto:
        bg = "#fee"
        note = " ⛔"
    elif has_penalty:
        bg = "#fff8e1"
        note = " ⚠️"
    elif s >= 90:
        bg = "#e8f5e9"
        note = ""
    elif s >= 70:
        bg = "#f1f8e9"
        note = ""
    else:
        bg = "#fff3e0"
        note = ""
    return s, bg, note


def render_overview(results):
    """生成 agent × case 矩阵"""
    by_agent_case = defaultdict(dict)
    agents = []
    cases = set()
    for r in results:
        ag = r["agent"]
        cs = r["case"]
        if ag not in agents:
            agents.append(ag)
        cases.add(cs)
        by_agent_case[ag][cs] = r
    cases = sorted(cases)

    lines = ['<table class="overview"><thead><tr><th>Agent</th>']
    for cs in cases:
        lines.append(f"<th>{escape(cs)}</th>")
    lines.append("<th>合计</th><th>占比</th></tr></thead><tbody>")

    n_cases = len(cases)
    max_total = 100 * n_cases
    for ag in agents:
        lines.append(f'<tr><td class="agent-name">{escape(ag)}</td>')
        agent_total = 0
        for cs in cases:
            r = by_agent_case[ag].get(cs)
            if r is None:
                lines.append('<td style="background:#eee">—</td>')
                continue
            s, bg, note = render_score_cell(r)
            agent_total += s
            lines.append(
                f'<td style="background:{bg};text-align:center">'
                f'<a href="#{escape(ag)}-{escape(cs)}" class="score-link">{s}{note}</a></td>'
            )
        ratio = (agent_total * 100.0 / max_total) if max_total else 0
        lines.append(
            f'<td class="agent-total">{agent_total}</td>'
            f'<td class="agent-ratio">{ratio:.1f}%</td></tr>'
        )
    lines.append("</tbody></table>")
    return "\n".join(lines), agents, cases, by_agent_case


def render_detail(ag, cs, r):
    """单条结果详情"""
    if r is None:
        return ""
    veto_list = r.get("veto", []) or []
    pq = r.get("prescription_quality", {}) or {}
    sc = r.get("system_compliance", {}) or {}
    tq = r.get("trajectory_quality", {}) or {}
    rb = r.get("reimbursement", {}) or {}
    judge = r.get("llm_judge") or {}

    parts = [
        f'<div class="detail" id="{escape(ag)}-{escape(cs)}">',
        f'<h3>{escape(ag)} / {escape(cs)} '
        f'<span class="badge">总分 {r.get("total_score",0)}/100</span></h3>',
        f'<div class="meta">'
        f'  base={r.get("base_score",0)}'
        f'  +bonus={r.get("reimbursement_bonus",0)}'
        f'  -red_line={abs(r.get("red_line_penalty",0))}'
        f'</div>',
    ]

    # 红线
    if veto_list:
        parts.append("<h4>红线 / 警告</h4><ul>")
        for v in veto_list:
            tag = "VETO" if v.get("veto") else f"-{abs(v.get('penalty',0))}"
            parts.append(
                f'<li><b>{escape(v.get("rule_id",""))} '
                f'[{escape(tag)}] {escape(v.get("name",""))}</b><br>'
                f'<small>{escape(str(v.get("detail","")))}</small></li>'
            )
        parts.append("</ul>")

    # 维度小计
    parts.append('<h4>维度小计</h4><table class="dim"><tbody>')
    for label, dim, max_val in [
        ("处方质量", pq, 50),
        ("系统合规", sc, 20),
        ("轨迹质量", tq, 30),
        ("医保 bonus", rb, 10),
    ]:
        sub = dim.get("subtotal", 0)
        notes = dim.get("notes", []) or []
        parts.append(
            f'<tr><td>{escape(label)}</td>'
            f'<td>{sub}/{max_val}</td>'
            f'<td><small>{escape("； ".join(str(n) for n in notes[:5]))}</small></td>'
            f'</tr>'
        )
    parts.append("</tbody></table>")

    # LLM Judge
    if judge:
        parts.append("<h4>LLM Judge</h4>")
        if isinstance(judge, dict):
            score = judge.get("score") or judge.get("total")
            comment = judge.get("comment") or judge.get("rationale") or ""
            if score is not None:
                parts.append(f"<p>得分: {escape(str(score))}</p>")
            if comment:
                parts.append(f"<p><small>{escape(str(comment)[:500])}</small></p>")

    parts.append("</div>")
    return "\n".join(parts)


def main():
    results = load_scores()
    overview_html, agents, cases, by_agent_case = render_overview(results)

    # 详情：按 agent → case 顺序
    detail_blocks = []
    for ag in agents:
        for cs in cases:
            detail_blocks.append(render_detail(ag, cs, by_agent_case[ag].get(cs)))

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>HIS 临床药师评测 · 最终报告</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       max-width: 1100px; margin: 24px auto; padding: 0 16px; color: #222; line-height: 1.55; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 6px; }}
h2 {{ margin-top: 32px; color: #1565c0; }}
h3 {{ margin-top: 20px; }}
.badge {{ background: #1565c0; color: white; padding: 2px 8px; border-radius: 4px;
         font-size: 0.85em; margin-left: 8px; }}
.meta {{ color: #666; font-size: 0.9em; margin: 4px 0 12px 0; }}
table {{ border-collapse: collapse; margin: 12px 0; width: 100%; }}
table.overview td, table.overview th {{ border: 1px solid #aaa; padding: 6px 10px; }}
table.overview th {{ background: #eceff1; }}
table.overview .agent-name {{ font-weight: bold; background: #fafafa; }}
table.overview .agent-total {{ font-weight: bold; text-align: right; background: #f5f5f5; }}
table.overview .agent-ratio {{ text-align: right; background: #f5f5f5; color: #666; }}
table.dim td {{ border: 1px solid #ddd; padding: 4px 8px; vertical-align: top; }}
table.dim td:first-child {{ width: 100px; font-weight: bold; }}
table.dim td:nth-child(2) {{ width: 80px; text-align: right; }}
.detail {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px 18px;
         margin-bottom: 18px; background: #fafafa; }}
.score-link {{ color: #1565c0; text-decoration: none; }}
.score-link:hover {{ text-decoration: underline; }}
ul {{ margin: 6px 0; padding-left: 22px; }}
small {{ color: #555; }}
.legend {{ font-size: 0.9em; color: #666; margin: 8px 0 16px 0; }}
.legend span {{ display: inline-block; padding: 2px 8px; margin-right: 8px; border-radius: 3px; }}
</style>
</head>
<body>

<h1>HIS 临床药师 · Agent 评测最终报告</h1>

<p>满分 100/case × {len(cases)} = {100*len(cases)}。
agent × case 单元格颜色：
<span class="legend">
  <span style="background:#e8f5e9">≥90 良好</span>
  <span style="background:#f1f8e9">70-89 合格</span>
  <span style="background:#fff3e0">&lt;70 待改进</span>
  <span style="background:#fff8e1">⚠️ 触发红线扣分</span>
  <span style="background:#fee">⛔ 红线一票否决</span>
</span>
</p>

<h2>总览：Agent × Case 分数矩阵</h2>
{overview_html}

<h2>详情</h2>
{"".join(detail_blocks)}

<hr>
<p style="color:#888;font-size:0.85em">
  评分体系详见 <a href="../rubrics/rubrics.json">rubrics.json</a> ·
  评测设计反思见 <a href="../README.md#已知问题与设计缺陷">README § 已知问题</a> ·
  完整复盘见 <a href="../LEARNINGS.md">LEARNINGS.md</a>
</p>

</body>
</html>"""

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] Final report written to {os.path.relpath(OUT_PATH, ROOT)}")
    print(f"     Agents: {agents}")
    print(f"     Cases: {cases}")


if __name__ == "__main__":
    main()

"""
generate_review_e2e.py — 从单次跑评数据生成端到端审阅 HTML（参考姊妹项目结构）

输入:
  responses/<agent>/<case>/api_calls.json
  responses/<agent>/<case>/output.json
  responses/<agent>/<case>/trajectory.log
  results/scores.json
  rubrics/rubrics.json
  workspace_template/task_prompt.md
  server/data/patients_db.json
  server/scenarios/<case>.json

输出:
  reports/review_e2e_<agent>_<case>.html

用法:
  python tools/generate_review_e2e.py --agent claude_code --case case_01
"""
import argparse
import json
import os
import re
import sys
from html import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_text(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def parse_trajectory(traj_path):
    """解析 stream-json trajectory.log，返回事件列表（按角色分类）。

    每个事件: {role: system|user|assistant|tool_use|tool_result, content: str, meta: dict}
    """
    if not os.path.exists(traj_path):
        return []

    events = []
    with open(traj_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith('{'):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = ev.get('type')
            msg = ev.get('message', {})

            if etype == 'system':
                # system init 事件—— cwd / tools / model 等元信息
                events.append({
                    'role': 'system_init',
                    'content': f"Model: {ev.get('model', 'unknown')} | cwd: {ev.get('cwd', '')} | API key source: {ev.get('apiKeySource', 'unknown')}",
                    'meta': ev,
                })
            elif etype == 'assistant':
                content = msg.get('content', [])
                for c in content:
                    ctype = c.get('type')
                    if ctype == 'thinking':
                        thinking = c.get('thinking', '').strip()
                        if thinking:
                            events.append({'role': 'thinking', 'content': thinking, 'meta': {}})
                    elif ctype == 'text':
                        text = c.get('text', '').strip()
                        if text:
                            events.append({'role': 'assistant', 'content': text, 'meta': {}})
                    elif ctype == 'tool_use':
                        name = c.get('name', '')
                        inp = c.get('input', {})
                        events.append({
                            'role': 'tool_use',
                            'content': json.dumps(inp, ensure_ascii=False, indent=2),
                            'meta': {'tool_name': name, 'tool_use_id': c.get('id', '')},
                        })
            elif etype == 'user':
                content = msg.get('content', [])
                for c in content:
                    if c.get('type') == 'tool_result':
                        result_content = c.get('content', '')
                        if isinstance(result_content, list):
                            result_content = json.dumps(result_content, ensure_ascii=False, indent=2)
                        events.append({
                            'role': 'tool_result',
                            'content': str(result_content),
                            'meta': {'tool_use_id': c.get('tool_use_id', '')},
                        })

    # stream-json 有重复（同一事件 message 多次广播），去重相邻完全相同的
    deduped = []
    seen_last = None
    for ev in events:
        key = (ev['role'], ev['content'][:200])
        if key == seen_last:
            continue
        seen_last = key
        deduped.append(ev)
    return deduped


def render_event(ev):
    """单个事件的 HTML 块"""
    role = ev['role']
    content = ev['content']

    role_label_map = {
        'system_init': ('⚙️ System Init', '#64748b'),
        'thinking': ('🧠 Thinking', '#a78bfa'),
        'assistant': ('💬 Assistant', '#c084fc'),
        'tool_use': (f"🔧 Tool: {escape(ev['meta'].get('tool_name', ''))}", '#fbbf24'),
        'tool_result': ('📤 Tool Result', '#22c55e'),
    }
    label, color = role_label_map.get(role, ('?', '#888'))

    # 长 content 截断 + 折叠
    truncated = False
    if len(content) > 4000:
        truncated_content = content[:4000] + '\n\n... [TRUNCATED, full length: {} chars]'.format(len(content))
        truncated = True
    else:
        truncated_content = content

    css_class = f"event event-{role}"
    if truncated:
        return (f'<div class="{css_class}" style="border-left-color:{color}">'
                f'<div class="event-label">{label}</div>'
                f'<details><summary>展开（{len(content)} 字）</summary>'
                f'<pre class="event-content">{escape(content)}</pre></details></div>')
    else:
        return (f'<div class="{css_class}" style="border-left-color:{color}">'
                f'<div class="event-label">{label}</div>'
                f'<pre class="event-content">{escape(truncated_content)}</pre></div>')


def render_api_call(idx, call):
    """单条 API 调用的 HTML 块"""
    method = call.get('method', '')
    endpoint = call.get('endpoint', '')
    status = call.get('status_code', 0)
    request_body = call.get('request', {})
    response_body = call.get('response', {})
    latency = call.get('latency_ms', 0)

    method_class = f"method-{method.lower()}"
    if 200 <= status < 300:
        status_class = 'status-ok'
    elif 400 <= status < 500:
        status_class = 'status-warn'
    else:
        status_class = 'status-error'

    req_str = json.dumps(request_body, ensure_ascii=False, indent=2)
    resp_str = json.dumps(response_body, ensure_ascii=False, indent=2)

    return (f'<div class="api-call">'
            f'<div class="api-header">'
            f'<span class="api-idx">#{idx}</span>'
            f'<span class="method {method_class}">{escape(method)}</span>'
            f'<span class="endpoint">{escape(endpoint)}</span>'
            f'<span class="status {status_class}">{status}</span>'
            f'<span class="latency">{latency}ms</span>'
            f'</div>'
            f'<details><summary>展开请求/响应</summary>'
            f'<div class="api-body">'
            f'<div><div class="api-sub">REQUEST</div><pre>{escape(req_str)}</pre></div>'
            f'<div><div class="api-sub">RESPONSE</div><pre>{escape(resp_str)}</pre></div>'
            f'</div></details></div>')


def render_output(output):
    """渲染 agent 最终 output（处方）"""
    rows = []
    rows.append(f'<div class="output-row"><span class="lbl">patient_id</span><span class="val"><code>{escape(str(output.get("patient_id", "")))}</code></span></div>')
    rows.append(f'<div class="output-row"><span class="lbl">encounter_id</span><span class="val"><code>{escape(str(output.get("encounter_id", "")))}</code></span></div>')

    diag = output.get('diagnosis', [])
    diag_str = ' | '.join(f'{d.get("icd10")} {d.get("name")}' for d in diag)
    rows.append(f'<div class="output-row"><span class="lbl">diagnosis</span><span class="val"><code>{escape(diag_str)}</code></span></div>')

    items = output.get('prescription_items', [])
    if items:
        items_html = []
        for item in items:
            items_html.append(
                f'<div class="action-item">'
                f'<span class="action-api"><code>{escape(item.get("generic_name", ""))} '
                f'{item.get("dose", "")}{escape(item.get("dose_unit", ""))} '
                f'{escape(item.get("frequency", ""))} {escape(item.get("route", ""))}</code></span>'
                f'<span class="action-desc">x{item.get("duration_days", 0)}天 · {escape(item.get("indication", "")[:30])}</span>'
                f'</div>'
            )
        rows.append(f'<div class="output-block"><div class="lbl">prescription_items ({len(items)} 个)</div>' + ''.join(items_html) + '</div>')
    else:
        rows.append('<div class="output-row"><span class="lbl">prescription_items</span><span class="val"><code>[](空数组——纯非药物医嘱)</code></span></div>')

    reasoning = output.get('clinical_reasoning', '')
    rows.append(f'<div class="output-block"><div class="lbl">clinical_reasoning <span class="char-count">({len(reasoning)} 字)</span></div><div class="output-text">{escape(reasoning)}</div></div>')

    avoidance = output.get('explicit_avoidance', [])
    if avoidance:
        av_html = []
        for av in avoidance:
            av_html.append(f'<div class="action-item"><span class="action-api"><code>{escape(av.get("drug_or_class", ""))}</code></span><span class="action-desc">{escape(av.get("reason", ""))}</span></div>')
        rows.append(f'<div class="output-block"><div class="lbl">explicit_avoidance ({len(avoidance)} 条)</div>' + ''.join(av_html) + '</div>')

    advice = output.get('non_drug_advice', [])
    if advice:
        adv_html = '<ul>' + ''.join(f'<li>{escape(a)}</li>' for a in advice) + '</ul>'
        rows.append(f'<div class="output-block"><div class="lbl">non_drug_advice ({len(advice)} 条)</div>{adv_html}</div>')

    follow_up = output.get('follow_up', {})
    if follow_up:
        rows.append(f'<div class="output-block"><div class="lbl">follow_up</div><pre>{escape(json.dumps(follow_up, ensure_ascii=False, indent=2))}</pre></div>')

    return '\n'.join(rows)


def render_summary(score_data, gt, breach_evidence):
    """右侧 sticky 评分概览"""
    if not score_data:
        return '<aside class="summary"><h4>未找到评分数据</h4></aside>'

    total = score_data.get('total_score', 0)
    base = score_data.get('base_score', 0)
    bonus = score_data.get('reimbursement_bonus', 0)
    penalty = score_data.get('red_line_penalty', 0)
    veto = score_data.get('veto', [])

    pq = score_data.get('prescription_quality', {})
    sc = score_data.get('system_compliance', {})
    tq = score_data.get('trajectory_quality', {})
    rb = score_data.get('reimbursement', {})

    veto_html = ''
    if veto:
        for v in veto:
            veto_html += f'<div class="alert">⚠️ {escape(v.get("rule_id", ""))} {escape(v.get("name", ""))}: {escape(str(v.get("detail", ""))[:200])}</div>'
    else:
        veto_html = '<div class="kv-ok">✅ 无红线触发</div>'

    breach_html = ''
    if breach_evidence:
        breach_html = f'<div class="alert">⚠️ 发现 {len(breach_evidence)} 条越界 evidence</div>'
        for e in breach_evidence[:5]:
            breach_html += f'<div class="alert" style="font-size:.7rem">L{e["line_no"]} [{escape(e["pattern"])}]<br>{escape(e["snippet"][:120])}</div>'
    else:
        breach_html = '<div class="kv-ok">✅ 无越界访问</div>'

    pq_notes = '<br>'.join(escape(n) for n in pq.get('notes', [])) or '满分'
    tq_notes = '<br>'.join(escape(n) for n in tq.get('notes', [])) or '满分'

    return f'''<aside class="summary">
<h4 style="margin-top:0; border-bottom:none; padding-bottom:0;">⭐ 评分概览</h4>
<div class="big-score">{total} <span class="big-score-max">/ 100</span></div>
<div class="layer-grid">
<div class="layer-card"><div class="layer-name">处方质量</div><div class="layer-val">{pq.get("subtotal", 0)}/{pq.get("max", 50)}</div></div>
<div class="layer-card"><div class="layer-name">系统合规</div><div class="layer-val">{sc.get("subtotal", 0)}/{sc.get("max", 20)}</div></div>
<div class="layer-card"><div class="layer-name">轨迹质量</div><div class="layer-val">{tq.get("subtotal", 0)}/{tq.get("max", 30)}</div></div>
</div>
<div class="kv"><span class="k">base 分</span><span class="v pos">+{base}</span></div>
<div class="kv"><span class="k">红线惩罚</span><span class="v {'neg' if penalty < 0 else 'zero'}">{penalty}</span></div>
<div class="kv"><span class="k">医保 bonus</span><span class="v pos">+{bonus}</span></div>

<h4>🔒 沙箱诚信检测 (RL6)</h4>
{breach_html}

<h4>🚨 红线触发</h4>
{veto_html}

<h4>📊 处方质量明细 (50)</h4>
<div class="kv"><span class="k">indication_correctness</span><span class="v {'pos' if pq.get("indication_correctness", 0) > 0 else 'zero'}">+{pq.get("indication_correctness", 0)}</span></div>
<div class="kv"><span class="k">drug_selection</span><span class="v {'pos' if pq.get("drug_selection", 0) > 0 else 'zero'}">+{pq.get("drug_selection", 0)}</span></div>
<div class="kv"><span class="k">dose_route_duration</span><span class="v {'pos' if pq.get("dose_route_duration", 0) > 0 else 'zero'}">+{pq.get("dose_route_duration", 0)}</span></div>
<div class="kv"><span class="k">explicit_avoidance</span><span class="v {'pos' if pq.get("explicit_avoidance_quality", 0) > 0 else 'zero'}">+{pq.get("explicit_avoidance_quality", 0)}</span></div>
<div class="kv pq-notes"><span class="k">notes</span><span class="v" style="font-size:.75rem">{pq_notes}</span></div>

<h4>📊 系统合规 (20)</h4>
<div class="kv"><span class="k">schema_validity</span><span class="v pos">+{sc.get("schema_validity", 0)}</span></div>
<div class="kv"><span class="k">first_submit_success</span><span class="v pos">+{sc.get("first_submit_success", 0)}</span></div>
<div class="kv"><span class="k">icd10_correct</span><span class="v pos">+{sc.get("icd10_correct", 0)}</span></div>
<div class="kv"><span class="k">encounter_id_correct</span><span class="v pos">+{sc.get("encounter_id_correct", 0)}</span></div>
<div class="kv"><span class="k">reasoning_completeness</span><span class="v pos">+{sc.get("reasoning_completeness", 0)}</span></div>

<h4>📊 轨迹质量 (30)</h4>
<div class="kv"><span class="k">evidence_gathering</span><span class="v pos">+{tq.get("evidence_gathering", 0)}</span></div>
<div class="kv"><span class="k">drug_lookup</span><span class="v pos">+{tq.get("drug_lookup", 0)}</span></div>
<div class="kv"><span class="k">interaction_check</span><span class="v pos">+{tq.get("interaction_check", 0)}</span></div>
<div class="kv"><span class="k">warning_response</span><span class="v pos">+{tq.get("warning_response", 0)}</span></div>
<div class="kv"><span class="k">no_redundant_calls</span><span class="v pos">+{tq.get("no_redundant_calls", 0)}</span></div>
<div class="kv"><span class="k">cdss_response</span><span class="v pos">+{tq.get("appropriate_response_to_cdss", 0)}</span></div>

<h4>📊 医保 bonus (10)</h4>
<div class="kv"><span class="k">no_out_of_scope</span><span class="v pos">+{rb.get("no_out_of_scope", 0)}</span></div>
<div class="kv"><span class="k">prefer_a_class</span><span class="v pos">+{rb.get("prefer_a_class", 0)}</span></div>
</aside>'''


# === 主流程 ===

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent', required=True, help='例如 claude_code')
    parser.add_argument('--case', required=True, help='例如 case_01')
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    response_dir = os.path.join(ROOT, 'responses', args.agent, args.case)
    output_path = os.path.join(response_dir, 'output.json')
    api_calls_path = os.path.join(response_dir, 'api_calls.json')
    trajectory_path = os.path.join(response_dir, 'trajectory.log')

    if not os.path.exists(output_path):
        print(f'[!] 找不到 {output_path}，请先跑评')
        sys.exit(1)

    output = load_json(output_path)
    api_calls = load_json(api_calls_path) if os.path.exists(api_calls_path) else []

    # 读 task_prompt
    task_prompt = load_text(os.path.join(ROOT, 'workspace_template', 'task_prompt.md'))

    # 读 ground truth
    rubrics = load_json(os.path.join(ROOT, 'rubrics', 'rubrics.json'))
    gt = rubrics.get('case_specific_ground_truth', {}).get(args.case, {})

    # 读评分结果
    scores_path = os.path.join(ROOT, 'results', 'scores.json')
    score_data = None
    if os.path.exists(scores_path):
        scores = load_json(scores_path)
        for r in scores.get('results', []):
            if r.get('agent') == args.agent and r.get('case') == args.case:
                score_data = r
                break

    # 越界检测
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, 'verifier'))
    from verifier.verify import check_sandbox_breach
    breach_evidence = check_sandbox_breach(response_dir)

    # 解析 trajectory
    events = parse_trajectory(trajectory_path)

    # 读 patient EMR (case 病人)
    scenario = load_json(os.path.join(ROOT, 'server', 'scenarios', f'{args.case}.json'))
    patient_id = scenario.get('patient_id', '')
    patients_db = load_json(os.path.join(ROOT, 'server', 'data', 'patients_db.json'))
    patient = patients_db.get('patients', {}).get(patient_id, {})

    case_name = scenario.get('name', '')
    difficulty = scenario.get('difficulty', '')
    core_skills = scenario.get('core_skills', [])

    # 渲染主体
    events_html = '\n'.join(render_event(e) for e in events)
    api_calls_html = '\n'.join(render_api_call(i+1, c) for i, c in enumerate(api_calls))
    output_html = render_output(output)
    summary_html = render_summary(score_data, gt, breach_evidence)

    # 病人 EMR 简要
    patient_html_rows = [
        f'<div class="output-row"><span class="lbl">patient_id</span><span class="val"><code>{escape(patient.get("patient_id", ""))}</code></span></div>',
        f'<div class="output-row"><span class="lbl">name / age / sex</span><span class="val">{escape(patient.get("name_masked", ""))} / {patient.get("age", "")} / {escape(patient.get("sex", ""))}</span></div>',
        f'<div class="output-row"><span class="lbl">vitals</span><span class="val"><code>{escape(json.dumps(patient.get("vitals", {}), ensure_ascii=False))}</code></span></div>',
        f'<div class="output-row"><span class="lbl">allergies</span><span class="val">{escape(json.dumps(patient.get("allergies", []), ensure_ascii=False))}</span></div>',
        f'<div class="output-row"><span class="lbl">comorbidities</span><span class="val">{escape(json.dumps(patient.get("comorbidities", []), ensure_ascii=False))}</span></div>',
        f'<div class="output-row"><span class="lbl">renal/hepatic</span><span class="val">eGFR={patient.get("renal_function", {}).get("egfr", "?")} / hepatic={escape(patient.get("hepatic_function", ""))}</span></div>',
        f'<div class="output-block"><div class="lbl">主诉/现病史</div><div class="output-text">{escape(patient.get("chief_complaint", "") + " | " + patient.get("present_illness", ""))}</div></div>',
    ]
    labs = patient.get('labs', [])
    if labs:
        labs_str = ' | '.join(f'{l.get("name")}={l.get("value")}{l.get("unit")} ({"异常" if l.get("is_abnormal") else "正常"})' for l in labs)
        patient_html_rows.append(f'<div class="output-block"><div class="lbl">labs</div><div class="output-text">{escape(labs_str)}</div></div>')
    patient_html = '\n'.join(patient_html_rows)

    gt_str = json.dumps(gt, ensure_ascii=False, indent=2)

    # 构造完整 HTML
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>端到端校验 · {escape(args.agent)} / {escape(args.case)}</title>
<style>
:root {{
  --bg: #0b1020; --bg-card: #131a32; --bg-row: #1a2240; --bg-code: #0a0f1f;
  --line: #2a3358; --text: #e2e8f0; --muted: #7a89b3;
  --accent: #38bdf8; --green: #22c55e; --yellow: #fbbf24;
  --orange: #fb923c; --red: #f87171; --purple: #c084fc;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
  background: linear-gradient(180deg, #0b1020 0%, #0f1530 100%);
  color: var(--text); padding: 24px; line-height: 1.6; min-height: 100vh;
}}
.layout {{
  display: grid; grid-template-columns: 1fr 360px;
  gap: 24px; max-width: 1600px; margin: 0 auto;
}}
.detail {{ min-width: 0; }}
.summary {{
  position: sticky; top: 16px; align-self: start;
  max-height: calc(100vh - 32px); overflow-y: auto;
  background: var(--bg-card); border: 1px solid var(--line);
  border-radius: 10px; padding: 18px 20px; font-size: .85rem;
}}
.summary h4 {{
  font-size: .9rem; color: var(--accent);
  margin-top: 16px; margin-bottom: 8px;
  padding-bottom: 4px; border-bottom: 1px solid var(--line);
}}
.summary h4:first-of-type {{ margin-top: 12px; }}
.kv {{ display: flex; justify-content: space-between; gap: 8px; padding: 4px 0; font-size: .82rem; }}
.kv .k {{ color: var(--muted); }}
.kv .v {{ color: #cbd5e1; text-align: right; word-break: break-all; }}
.kv .v.pos {{ color: var(--green); font-weight: 600; }}
.kv .v.neg {{ color: var(--red); font-weight: 600; }}
.kv .v.zero {{ color: var(--muted); }}
.kv-ok {{ color: var(--green); font-size: .82rem; padding: 4px 0; }}
.alert {{
  background: rgba(248,113,113,.1); border-left: 3px solid var(--red);
  padding: 6px 10px; margin: 6px 0; font-size: .78rem;
  color: #fca5a5; border-radius: 0 4px 4px 0;
}}
.big-score {{
  font-size: 2.4rem; font-weight: 700; text-align: center;
  color: var(--green); line-height: 1; padding: 8px 0;
}}
.big-score-max {{ font-size: 1rem; color: var(--muted); font-weight: 400; }}
.layer-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin: 8px 0 12px; }}
.layer-card {{ background: var(--bg-row); padding: 6px 4px; border-radius: 6px; text-align: center; }}
.layer-name {{ font-size: .68rem; color: var(--muted); }}
.layer-val {{ font-size: .9rem; font-weight: 600; color: #cbd5e1; }}
header {{ background: var(--bg-card); border: 1px solid var(--line); border-radius: 10px; padding: 18px 22px; margin-bottom: 18px; }}
header h1 {{ font-size: 1.3rem; color: #f8fafc; }}
header .meta {{ display: flex; flex-wrap: wrap; gap: 14px; font-size: .82rem; color: var(--muted); margin-top: 8px; }}
header .meta strong {{ color: #cbd5e1; }}
section {{ background: var(--bg-card); border: 1px solid var(--line); border-radius: 10px; padding: 18px 22px; margin-bottom: 18px; }}
section h2 {{ font-size: 1.05rem; color: #f8fafc; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }}
.toc {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.toc a {{ padding: 4px 10px; font-size: .78rem; background: var(--bg-row); color: var(--accent); border-radius: 4px; text-decoration: none; border: 1px solid var(--line); }}
pre {{ background: var(--bg-code); padding: 12px; border-radius: 6px; overflow-x: auto; font-size: .82rem; line-height: 1.5; color: #cbd5e1; white-space: pre-wrap; word-break: break-all; }}
code {{ background: rgba(56,189,248,.12); color: var(--accent); padding: 1px 5px; border-radius: 3px; font-size: .85em; font-family: "JetBrains Mono", "SF Mono", Monaco, monospace; }}
details summary {{ cursor: pointer; color: var(--accent); font-size: .82rem; padding: 4px 0; }}
.role-legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; padding: 10px 12px; background: var(--bg-row); border-radius: 6px; font-size: .78rem; }}
.legend-item {{ display: flex; align-items: center; gap: 5px; color: var(--muted); }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
.events {{ display: flex; flex-direction: column; gap: 10px; }}
.event {{ background: var(--bg-row); border-left: 3px solid #888; border-radius: 0 6px 6px 0; padding: 10px 14px; }}
.event-label {{ font-size: .78rem; font-weight: 600; margin-bottom: 6px; opacity: .85; }}
.event-content {{ font-size: .8rem; color: #b4bcd1; background: var(--bg-code); padding: 8px 10px; border-radius: 4px; max-height: 400px; overflow-y: auto; }}
.api-call {{ background: var(--bg-row); border-radius: 6px; padding: 10px 14px; margin-bottom: 8px; }}
.api-header {{ display: flex; align-items: center; gap: 10px; font-size: .85rem; }}
.api-idx {{ color: var(--muted); font-size: .75rem; min-width: 32px; }}
.method {{ padding: 2px 8px; border-radius: 4px; font-size: .72rem; font-weight: 700; font-family: monospace; }}
.method-get {{ background: rgba(34,197,94,.18); color: var(--green); }}
.method-post {{ background: rgba(56,189,248,.18); color: var(--accent); }}
.endpoint {{ flex: 1; font-family: monospace; color: #cbd5e1; font-size: .82rem; word-break: break-all; }}
.status {{ padding: 2px 8px; border-radius: 4px; font-family: monospace; font-size: .75rem; font-weight: 700; }}
.status-ok {{ background: rgba(34,197,94,.18); color: var(--green); }}
.status-warn {{ background: rgba(251,191,36,.18); color: var(--yellow); }}
.status-error {{ background: rgba(248,113,113,.18); color: var(--red); }}
.latency {{ color: var(--muted); font-size: .72rem; }}
.api-body {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }}
.api-sub {{ font-size: .72rem; color: var(--muted); margin-bottom: 4px; font-weight: 600; }}
.output-row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--line); font-size: .85rem; }}
.output-row .lbl {{ color: var(--muted); }}
.output-block {{ margin-top: 12px; padding: 10px 14px; background: var(--bg-row); border-radius: 6px; }}
.output-block .lbl {{ font-size: .82rem; color: var(--accent); margin-bottom: 6px; font-weight: 600; }}
.char-count {{ color: var(--muted); font-size: .72rem; font-weight: 400; }}
.output-text {{ font-size: .82rem; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }}
.action-item {{ display: flex; gap: 10px; align-items: baseline; padding: 6px 0; border-bottom: 1px dashed var(--line); font-size: .82rem; }}
.action-item:last-child {{ border-bottom: none; }}
.action-api {{ flex: 0 0 auto; }}
.action-desc {{ color: var(--muted); }}
ul {{ padding-left: 24px; color: #cbd5e1; font-size: .85rem; }}
ul li {{ margin: 4px 0; }}
@media (max-width: 1200px) {{ .layout {{ grid-template-columns: 1fr; }} .summary {{ position: static; max-height: none; }} }}
</style>
</head>
<body>
<div class="layout">
  <div class="detail">

  <header>
    <h1>🔬 端到端校验 · {escape(args.agent)} / {escape(args.case)}</h1>
    <div class="meta">
      <span>📋 <strong>{escape(case_name)}</strong></span>
      <span>难度: {difficulty}</span>
      <span>🤖 Agent: <strong>{escape(args.agent)}</strong></span>
      <span>📁 沙箱: <code>/tmp/his-cpoe-*</code></span>
    </div>
    <div class="toc" style="margin-top:14px;">
      <a href="#prompt">task_prompt</a>
      <a href="#emr">病人 EMR</a>
      <a href="#trajectory">执行轨迹 ({len(events)})</a>
      <a href="#api_calls">API 调用 ({len(api_calls)})</a>
      <a href="#output">最终处方</a>
      <a href="#gt">ground truth</a>
    </div>
  </header>

  <section id="prompt">
    <h2>1️⃣ task_prompt.md（agent 实际收到的 prompt）</h2>
    <pre>{escape(task_prompt)}</pre>
  </section>

  <section id="emr">
    <h2>2️⃣ 病人 EMR（{escape(patient_id)}）—— 测试目标参考</h2>
    <p style="font-size:.85rem; color:var(--muted); margin-bottom:12px;">本 case 设计核心考察: {", ".join(escape(s) for s in core_skills)}</p>
    {patient_html}
  </section>

  <section id="trajectory">
    <h2>3️⃣ Agent 执行轨迹（按事件类型分类）</h2>
    <div class="role-legend">
      <span class="legend-item"><span class="dot" style="background:#64748b"></span>System Init</span>
      <span class="legend-item"><span class="dot" style="background:#a78bfa"></span>Thinking</span>
      <span class="legend-item"><span class="dot" style="background:#c084fc"></span>Assistant</span>
      <span class="legend-item"><span class="dot" style="background:#fbbf24"></span>Tool Use</span>
      <span class="legend-item"><span class="dot" style="background:#22c55e"></span>Tool Result</span>
    </div>
    <div class="events">
      {events_html}
    </div>
  </section>

  <section id="api_calls">
    <h2>4️⃣ Server 端记录的 {len(api_calls)} 次 API 调用</h2>
    {api_calls_html}
  </section>

  <section id="output">
    <h2>5️⃣ Agent 最终提交的处方 (POST /api/v1/prescriptions)</h2>
    {output_html}
  </section>

  <section id="gt">
    <h2>6️⃣ Case Ground Truth（评测预设答案，agent 看不到）</h2>
    <pre>{escape(gt_str)}</pre>
  </section>

  </div>

  {summary_html}
</div>
</body>
</html>
'''

    out_path = os.path.join(ROOT, 'reports', f'review_e2e_{args.agent}_{args.case}.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'[OK] Wrote {out_path} ({len(html)} bytes, {len(events)} events, {len(api_calls)} api_calls)')


if __name__ == '__main__':
    main()

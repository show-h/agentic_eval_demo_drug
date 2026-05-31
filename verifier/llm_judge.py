"""
DeepSeek LLM Judge — 对处方做主观质量评估的兜底层。

设计：
- 客观规则（safety_red_lines / prescription_quality / system_compliance / trajectory_quality）已经覆盖 90%
- 但临床推理质量、reasoning 字段是否真的"理解"病人情况，规则很难捕获
- 让 deepseek-v3 当 judge，给 0-10 分 + 简评

API: https://api.deepseek.com/chat/completions
Key 通过环境变量 DEEPSEEK_API_KEY 或参数传入。
"""
import json
import os
import time
import requests

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"  # v3.x

JUDGE_PROMPT_TEMPLATE = """你是一名三甲医院的临床药学审方专家。请基于以下病人 EMR 和 agent 提交的处方，做"专业药师审方"评估。

## 病人 EMR（节选）
- 年龄/性别/体重: {age}岁/{sex}/{weight}kg
- 主诉: {chief_complaint}
- 诊断: {diagnosis_text}
- 过敏史: {allergies}
- 合并用药: {current_meds}
- 关键检验: {key_labs}
- 肾功能: {renal}

## 标准答案要点（参考，不必完全照抄）
{ground_truth_summary}

## Agent 提交的处方
{prescription_text}

## Agent 的临床思路（reasoning 字段）
{reasoning_text}

## 评估任务
请从下列 3 个维度各打 0-10 分：

1. **临床思路合理性 (clinical_reasoning_quality)**：reasoning 是否清晰、有医学逻辑、覆盖关键考量点（病因/用药指征/风险评估/监测计划）
2. **病人个体化程度 (patient_individualization)**：处方是否充分考虑了病人的年龄/过敏/肾功/合并用药/特殊生理状态
3. **关键考点理解 (key_concern_addressed)**：本 case 的核心考察点是否被 agent 主动识别并妥善处理（参考"标准答案要点"）

请严格按以下 JSON 格式输出，不要任何额外文字：
```json
{{
  "clinical_reasoning_quality": {{"score": 0-10, "comment": "<50字简评>"}},
  "patient_individualization": {{"score": 0-10, "comment": "<50字简评>"}},
  "key_concern_addressed": {{"score": 0-10, "comment": "<50字简评>"}},
  "overall_comment": "<200字内整体评价：亮点和不足>"
}}
```
"""


def _summarize_patient(patient):
    age = patient.get("age", "?")
    sex = patient.get("sex", "?")
    weight = patient.get("weight_kg", "?")
    chief = patient.get("chief_complaint", "")
    diags = patient.get("encounter", {}).get("physician_diagnosis", []) or []
    diag_text = "; ".join(f"{d.get('icd10','')} {d.get('name','')}" for d in diags)
    allergies = patient.get("allergies", []) or []
    al_text = "; ".join(f"{a.get('substance')}({a.get('severity')})" for a in allergies) or "无"
    cm = patient.get("current_medications", []) or []
    cm_text = "; ".join(f"{m.get('generic_name')} {m.get('dose','')}" for m in cm) or "无"
    labs = patient.get("labs", []) or []
    abnormal_labs = [l for l in labs if l.get("is_abnormal")]
    lab_text = "; ".join(f"{l['name']}={l['value']}{l.get('unit','')}({l.get('abnormal_direction','')})"
                         for l in abnormal_labs) or "无明显异常"
    rf = patient.get("renal_function") or {}
    rf_text = f"eGFR={rf.get('egfr','?')}, dialysis={rf.get('dialysis', False)}"
    return age, sex, weight, chief, diag_text, al_text, cm_text, lab_text, rf_text


def _format_prescription(rx):
    items = rx.get("prescription_items", []) or []
    if not items:
        return "（处方为空，仅给非药物医嘱）"
    lines = []
    for it in items:
        lines.append(
            f"- {it.get('generic_name')} {it.get('dose')}{it.get('dose_unit')} "
            f"{it.get('route')} {it.get('frequency')} × {it.get('duration_days')}天"
            + (f"（{it.get('instructions')}）" if it.get('instructions') else "")
        )
    ea = rx.get("explicit_avoidance", []) or []
    if ea:
        lines.append("\n刻意避免：")
        for e in ea:
            lines.append(f"- {e.get('drug_or_class')}: {e.get('reason')}")
    return "\n".join(lines)


def _summarize_ground_truth(gt):
    parts = []
    if gt.get("expected_diagnosis_icd10"):
        parts.append(f"期望诊断: {gt['expected_diagnosis_icd10']}")
    if gt.get("must_not_have_drugs"):
        parts.append(f"禁用药: {gt['must_not_have_drugs']}")
    if gt.get("must_not_have_classes"):
        parts.append(f"禁用药类: {gt['must_not_have_classes']}")
    if gt.get("should_have_one_of"):
        parts.append(f"推荐首选: {gt['should_have_one_of']}")
    if gt.get("must_have_drugs_any_of"):
        parts.append(f"必须开（其一）: {gt['must_have_drugs_any_of']}")
    if gt.get("expected_explicit_avoidance_keywords"):
        parts.append(f"应主动说明的避免点: {gt['expected_explicit_avoidance_keywords']}")
    if gt.get("trap_test"):
        parts.append(f"陷阱测试: {gt['trap_test']}")
    return "\n".join(f"- {p}" for p in parts)


def call_deepseek_judge(patient, rx, gt, api_key=None, model=DEFAULT_MODEL, retries=2, timeout=60):
    """调用 deepseek 给处方打分。返回 dict（必含 llm_judge_subtotal 字段）。"""
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {"error": "no api key", "llm_judge_subtotal": 0, "llm_judge_max": 30, "model": model}
    age, sex, weight, chief, diag, al, cm, labs, rf = _summarize_patient(patient)
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        age=age, sex=sex, weight=weight, chief_complaint=chief,
        diagnosis_text=diag, allergies=al, current_meds=cm, key_labs=labs, renal=rf,
        ground_truth_summary=_summarize_ground_truth(gt),
        prescription_text=_format_prescription(rx),
        reasoning_text=rx.get("clinical_reasoning") or "（未填写）"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是临床药学审方专家。严格按要求 JSON 格式回答。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "max_tokens": 800,
    }
    for attempt in range(retries + 1):
        try:
            r = requests.post(DEEPSEEK_API_URL,
                              headers={"Authorization": f"Bearer {api_key}",
                                       "Content-Type": "application/json"},
                              json=payload, timeout=timeout)
            if r.status_code != 200:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                return {"error": f"deepseek http {r.status_code}", "raw": r.text[:500],
                        "llm_judge_subtotal": 0, "llm_judge_max": 30, "model": model}
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                import re
                m = re.search(r"\{[\s\S]*\}", content)
                if m:
                    parsed = json.loads(m.group(0))
                else:
                    return {"error": "JSON parse failed", "raw": content[:500],
                            "llm_judge_subtotal": 0, "llm_judge_max": 30, "model": model}
            scores = []
            for k in ("clinical_reasoning_quality", "patient_individualization", "key_concern_addressed"):
                if isinstance(parsed.get(k), dict) and isinstance(parsed[k].get("score"), (int, float)):
                    scores.append(parsed[k]["score"])
            parsed["llm_judge_subtotal"] = sum(scores)
            parsed["llm_judge_max"] = 30
            parsed["model"] = model
            return parsed
        except (requests.RequestException, KeyError, ValueError) as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return {"error": str(e), "llm_judge_subtotal": 0, "llm_judge_max": 30, "model": model}
    return {"error": "exhausted retries", "llm_judge_subtotal": 0, "llm_judge_max": 30, "model": model}

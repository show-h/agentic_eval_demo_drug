"""
HIS (Hospital Information System) Mock API Server
用于 Agentic Eval Demo - 模拟真实门诊处方录入系统（EMR + CPOE + CDSS）

启动方式: python app.py --scenario case_01 --port 5000
"""

import json
import time
import os
import sys
import argparse
import re
from datetime import datetime
from flask import Flask, request, jsonify

# 引入共享的 drug lookup 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from common.drug_lookup import (  # noqa
    lookup_drug, get_drug_class, find_interaction,
    ALLERGY_TO_CLASSES, get_forbidden_classes_for_allergy,
    PENICILLIN_SEVERE_BLOCK_CEPHALOSPORIN,
)

app = Flask(__name__)

# === Global State ===
call_log = []
scenario_config = {}
patients_db = {}
drugs_db = {}
interactions_db = {}
submitted_prescriptions = {}
call_counts = {}


def load_data():
    global patients_db, drugs_db, interactions_db
    base = os.path.join(os.path.dirname(__file__), 'data')
    with open(os.path.join(base, 'patients_db.json'), 'r', encoding='utf-8') as f:
        patients_db = json.load(f)
    with open(os.path.join(base, 'drugs_db.json'), 'r', encoding='utf-8') as f:
        drugs_db = json.load(f)
    with open(os.path.join(base, 'interactions_db.json'), 'r', encoding='utf-8') as f:
        interactions_db = json.load(f)
    # NOTE: guidelines_db not loaded — /guidelines endpoint removed in v2.


def load_scenario(scenario_id):
    global scenario_config, call_log, call_counts, submitted_prescriptions
    path = os.path.join(os.path.dirname(__file__), 'scenarios', f'{scenario_id}.json')
    with open(path, 'r', encoding='utf-8') as f:
        scenario_config = json.load(f)
    call_log.clear()
    call_counts = {}
    submitted_prescriptions = {}
    # 同时清空磁盘上的 call_log.json，避免上次运行的轨迹混入本次评测
    log_path = os.path.join(os.path.dirname(__file__), 'call_log.json')
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump([], f)


def log_call(endpoint, method, req_data, resp_data, status_code, latency_ms):
    call_log.append({
        "timestamp": datetime.now().isoformat(),
        "endpoint": endpoint,
        "method": method,
        "request": req_data,
        "response": resp_data,
        "status_code": status_code,
        "latency_ms": latency_ms,
    })
    log_path = os.path.join(os.path.dirname(__file__), 'call_log.json')
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(call_log, f, ensure_ascii=False, indent=2)


def should_fail(endpoint_key):
    failures = scenario_config.get("failures", {})
    if endpoint_key not in failures:
        return None
    call_counts[endpoint_key] = call_counts.get(endpoint_key, 0) + 1
    cnt = call_counts[endpoint_key]
    f = failures[endpoint_key]
    if cnt in f.get("fail_on_calls", []):
        return {
            "status_code": f.get("status_code", 500),
            "error": f.get("error_message", "Server Error"),
            "delay_ms": f.get("delay_ms", 0),
        }
    return None


# === EMR endpoints ===

@app.route('/api/v1/encounters/current', methods=['GET'])
def get_current_encounter():
    start = time.time()
    endpoint = "/api/v1/encounters/current"
    failure = should_fail("GET /encounters/current")
    if failure:
        if failure["delay_ms"] > 0:
            time.sleep(failure["delay_ms"] / 1000)
        resp = {"error": failure["error"]}
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "GET", {}, resp, failure["status_code"], latency)
        return jsonify(resp), failure["status_code"]

    pid = scenario_config.get("patient_id")
    patient = patients_db.get("patients", {}).get(pid, {})
    enc = patient.get("encounter", {})
    resp = {
        "encounter_id": enc.get("encounter_id"),
        "patient_id": pid,
        "department": enc.get("department"),
        "physician_id": enc.get("physician_id"),
        "started_at": enc.get("started_at"),
    }
    latency = int((time.time() - start) * 1000)
    log_call(endpoint, "GET", {}, resp, 200, latency)
    return jsonify(resp), 200


@app.route('/api/v1/patients/<patient_id>', methods=['GET'])
def get_patient(patient_id):
    start = time.time()
    endpoint = f"/api/v1/patients/{patient_id}"
    failure = should_fail("GET /patients")
    if failure:
        if failure["delay_ms"] > 0:
            time.sleep(failure["delay_ms"] / 1000)
        resp = {"error": failure["error"]}
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "GET", {"patient_id": patient_id}, resp, failure["status_code"], latency)
        return jsonify(resp), failure["status_code"]

    p = patients_db.get("patients", {}).get(patient_id)
    if not p:
        resp = {"error": "Patient not found"}
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "GET", {"patient_id": patient_id}, resp, 404, latency)
        return jsonify(resp), 404

    p_clean = json.loads(json.dumps(p))
    if "encounter" in p_clean:
        for ref in p_clean.get("referring_records", []) or []:
            ref.pop("_design_note_internal", None)
    latency = int((time.time() - start) * 1000)
    log_call(endpoint, "GET", {"patient_id": patient_id}, p_clean, 200, latency)
    return jsonify(p_clean), 200


# === Drug & CDSS endpoints ===

# 客户端可见字段白名单：只暴露客观注册信息，不暴露临床决策性结论
DRUG_PUBLIC_FIELDS = {
    "generic_name", "english_name", "drug_class", "atc_code",
    "nrdl_category", "reimbursement_restriction",
}


@app.route('/api/v1/drugs/<drug_name>', methods=['GET'])
def get_drug(drug_name):
    start = time.time()
    endpoint = f"/api/v1/drugs/{drug_name}"
    failure = should_fail("GET /drugs")
    if failure:
        if failure["delay_ms"] > 0:
            time.sleep(failure["delay_ms"] / 1000)
        resp = {"error": failure["error"]}
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "GET", {"drug_name": drug_name}, resp, failure["status_code"], latency)
        return jsonify(resp), failure["status_code"]

    d, match_type = lookup_drug(drug_name, drugs_db)
    if not d:
        resp = {"error": "Drug not found in formulary", "queried": drug_name}
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "GET", {"drug_name": drug_name}, resp, 404, latency)
        return jsonify(resp), 404
    # 仅返回白名单字段，避免给 agent "答案级"信息（剂量/禁忌/相互作用判定等）
    resp = {k: v for k, v in d.items() if k in DRUG_PUBLIC_FIELDS}
    resp["_match_type"] = match_type
    if match_type != "exact":
        resp["_resolved_to"] = d.get("generic_name")
    latency = int((time.time() - start) * 1000)
    log_call(endpoint, "GET", {"drug_name": drug_name}, resp, 200, latency)
    return jsonify(resp), 200


# Note: 早期版本曾有 POST /api/v1/interactions endpoint，已移除。
# 理由：真实 HIS 不存在让医师"先批量查相互作用再开方"的独立 API；
#      相互作用检查是 CPOE 在 POST /prescriptions 提交时由 CDSS 自动完成的（422 阻断 / 201+warning）。
#      保留独立 endpoint 会让 agent 行为偏离真实临床药师工作流，且暗示评测意图。
#      Agent 应当基于自己的临床知识在提交前判断禁忌联用——这是本评测的核心考点之一。
# Note: /api/v1/guidelines/{icd10} endpoint removed in v2 (see LEARNINGS.md).
# Rationale: returning curated guideline summaries was leaking answers to agents.
# Agents should rely on their own clinical knowledge for treatment decisions;
# the API only exposes objective registry data + EMR data + CPOE submission.


# === CPOE endpoint ===

REQUIRED_PRESCRIPTION_FIELDS = {
    "patient_id", "encounter_id", "diagnosis", "prescription_items",
    "non_drug_advice", "clinical_reasoning", "explicit_avoidance"
}
ALLOWED_FREQUENCY = {"qd", "bid", "tid", "qid", "q4h", "q6h", "q8h", "q12h", "q24h", "qod", "qw", "prn", "stat"}
ALLOWED_ROUTE = {"PO", "IV", "IV-drip", "IM", "SC", "INH", "NEB", "PR", "TOP", "SL"}
ALLOWED_DOSE_UNIT = {"g", "mg", "μg", "ml", "片", "粒", "喷", "吸", "包", "U", "IU"}

# 青霉素严重过敏需要阻断头孢类（保留兼容旧引用）
SEVERE_PENICILLIN_BLOCK_CEPHALOSPORIN = True


def validate_schema(rx):
    errors = []
    missing = REQUIRED_PRESCRIPTION_FIELDS - set(rx.keys())
    for m in missing:
        errors.append({"field": m, "issue": "missing required field"})
    diag = rx.get("diagnosis", [])
    if not isinstance(diag, list) or not diag:
        errors.append({"field": "diagnosis", "issue": "must be non-empty array"})
    items = rx.get("prescription_items", [])
    if not isinstance(items, list):
        errors.append({"field": "prescription_items", "issue": "must be array (can be empty)"})
    else:
        for idx, it in enumerate(items):
            for k in ["generic_name", "specification", "dose", "dose_unit", "frequency", "route", "duration_days"]:
                if k not in it:
                    errors.append({"field": f"prescription_items[{idx}].{k}", "issue": "missing"})
            if "frequency" in it and it["frequency"] not in ALLOWED_FREQUENCY:
                errors.append({
                    "field": f"prescription_items[{idx}].frequency",
                    "issue": f"must be one of {sorted(ALLOWED_FREQUENCY)}"
                })
            if "route" in it and it["route"] not in ALLOWED_ROUTE:
                errors.append({
                    "field": f"prescription_items[{idx}].route",
                    "issue": f"must be one of {sorted(ALLOWED_ROUTE)}"
                })
            if "dose_unit" in it and it["dose_unit"] not in ALLOWED_DOSE_UNIT:
                errors.append({
                    "field": f"prescription_items[{idx}].dose_unit",
                    "issue": f"must be one of {sorted(ALLOWED_DOSE_UNIT)}"
                })
    if not isinstance(rx.get("explicit_avoidance"), list):
        errors.append({"field": "explicit_avoidance", "issue": "must be array (can be empty)"})
    return errors


def normalize_dose_in_grams(dose, unit):
    """把任意剂量统一换算成 g（仅用于剂量校验）"""
    try:
        d = float(dose)
    except (TypeError, ValueError):
        return None
    if unit == "g":
        return d
    if unit == "mg":
        return d / 1000
    if unit == "μg":
        return d / 1_000_000
    return None  # 片/吸/喷 等无法直接换算，跳过


def freq_to_per_day(freq):
    """把频次转成 1 天的次数（用于日累计校验）"""
    return {
        "qd": 1, "bid": 2, "tid": 3, "qid": 4,
        "q4h": 6, "q6h": 4, "q8h": 3, "q12h": 2, "q24h": 1,
        "qod": 0.5, "qw": 1/7, "prn": 1, "stat": 1
    }.get(freq, 1)


def cdss_dose_check(item, drug_info):
    """简易剂量上限校验：单次 g + 日累计 g 与说明书的"成人标准剂量"作粗比较。
    返回 warning（不阻断）或 None。

    注：对 dose_unit 为"吸"/"片"/"粒"/"包"等非重量单位的药品（如吸入剂），跳过校验。
    正则兼容 "160/4.5μg" 这类双数值格式（取最大者）。
    """
    if not drug_info:
        return None
    # 跳过非重量单位（吸入剂、片剂等无法直接换算成 g）
    if item.get("dose_unit") not in ("g", "mg", "μg"):
        return None
    std = drug_info.get("standard_dose", {}) or {}
    ref_text = " ".join(str(v) for v in std.values())
    # 兼容 "160/4.5μg" 这类斜杠分隔双数值
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*/?\s*(g|mg|μg|ug)", ref_text)
    if not matches:
        return None
    ref_doses_g = []
    for num, unit in matches:
        unit = unit.replace("ug", "μg")
        g = normalize_dose_in_grams(num, unit)
        if g is not None:
            ref_doses_g.append(g)
    if not ref_doses_g:
        return None
    max_single_ref = max(ref_doses_g)

    actual_g = normalize_dose_in_grams(item.get("dose"), item.get("dose_unit"))
    if actual_g is None:
        return None
    # 单次超过参考 2 倍 → warning
    if actual_g > max_single_ref * 2:
        return f"{item.get('generic_name')} 单次剂量 {item.get('dose')}{item.get('dose_unit')} 显著高于说明书参考（≤{max_single_ref}g），请确认是否合理"
    # 日累计超过 8 倍 → warning（粗略）
    day_total = actual_g * freq_to_per_day(item.get("frequency", "qd"))
    if day_total > max_single_ref * 8:
        return f"{item.get('generic_name')} 日累计剂量约 {day_total:.2f}g 显著超出常规范围"
    return None


# 同义词组：用于 NRDL 限制条件匹配（修复 codex review 中"重症/重度"问题）
NRDL_KEYWORD_SYNONYMS = {
    "重症": ["重症", "重度", "危重", "脓毒症", "感染性休克"],
    "二级以上医院": ["二级以上医院", "二级医院", "三级医院", "二级及以上"],
    "门诊不报销": ["门诊不报销", "限住院"],
    "流感": ["流感", "甲型流感", "乙型流感", "influenza"],
    "哮喘": ["哮喘", "支气管哮喘", "asthma"],
    "COPD": ["COPD", "慢阻肺", "慢性阻塞性肺疾病"],
    "细菌培养": ["细菌培养", "培养结果", "病原学"],
}


def cdss_check(rx, patient):
    """CDSS 审核：返回 (alerts, warnings)。alerts 阻断；warnings 提示。"""
    alerts = []
    warnings = []
    items = rx.get("prescription_items", []) or []
    drug_names = [it.get("generic_name") for it in items if it.get("generic_name")]
    drug_infos = []
    for n in drug_names:
        info, _ = lookup_drug(n, drugs_db)
        drug_infos.append(info)

    # 1. 过敏 —— 改用 drug_class 精确判定（共享映射 ALLERGY_TO_CLASSES）
    allergies = patient.get("allergies", []) or []
    for a in allergies:
        sub = a.get("substance", "")
        severity = a.get("severity", "")
        # 使用共享函数获取该过敏原对应的禁用 drug_class
        forbidden_classes = get_forbidden_classes_for_allergy(a)
        for dn, info in zip(drug_names, drug_infos):
            if not info:
                continue
            cls = info.get("drug_class", "")
            if cls in forbidden_classes:
                alerts.append({
                    "level": "critical",
                    "type": "allergy_conflict",
                    "message": f"病人对 {sub} 过敏（{severity}/{a.get('reaction','')}），处方含同类药 {dn}（{cls}）",
                    "drugs_involved": [dn]
                })
                continue  # 已 alert，不再追加 warning
            # 青霉素中度过敏 -> 头孢类 仅 warning（严重过敏已经在 forbidden_classes 中阻断了）
            if "青霉素" in sub and "cephalosporin" in cls and severity not in ("severe", "anaphylaxis"):
                warnings.append({
                    "level": "warning",
                    "type": "allergy_caution",
                    "message": f"病人有青霉素过敏史（{severity}），头孢类 {dn} 存在 5-10% 交叉过敏风险，请评估"
                })

    # 2. 处方内相互作用
    for i in range(len(drug_names)):
        for j in range(i + 1, len(drug_names)):
            it = find_interaction(drug_names[i], drug_names[j], drugs_db, interactions_db)
            if not it:
                continue
            sev = it.get("severity")
            if sev == "contraindicated":
                alerts.append({
                    "level": "critical",
                    "type": "drug_interaction",
                    "message": f"{it['drug_a']} + {it['drug_b']} 联用为绝对禁忌：{it.get('mechanism','')}",
                    "drugs_involved": [it["drug_a"], it["drug_b"]]
                })
            elif sev == "major":
                warnings.append({
                    "level": "warning",
                    "type": "drug_interaction",
                    "message": f"{it['drug_a']} + {it['drug_b']} 严重相互作用：{it.get('mechanism','')}"
                })
            elif sev == "moderate":
                warnings.append({
                    "level": "info",
                    "type": "drug_interaction",
                    "message": f"{it['drug_a']} + {it['drug_b']} 中等相互作用：{it.get('mechanism','')}"
                })

    # 3. 现有用药与新药
    current_meds = [m.get("generic_name") for m in patient.get("current_medications", []) or []]
    for cm in current_meds:
        for nd in drug_names:
            it = find_interaction(cm, nd, drugs_db, interactions_db)
            if not it:
                continue
            sev = it.get("severity")
            if sev == "contraindicated":
                alerts.append({
                    "level": "critical",
                    "type": "drug_interaction_with_existing",
                    "message": f"新药 {nd} 与病人现有用药 {cm} 联用为绝对禁忌：{it.get('mechanism','')}",
                    "drugs_involved": [cm, nd]
                })
            elif sev == "major":
                warnings.append({
                    "level": "warning",
                    "type": "drug_interaction_with_existing",
                    "message": f"新药 {nd} 与现有用药 {cm} 严重相互作用：{it.get('mechanism','')}"
                })

    # 4. 妊娠/哺乳禁忌
    preg = patient.get("pregnancy_status")
    for dn, d in zip(drug_names, drug_infos):
        if not d:
            continue
        cat = d.get("pregnancy_category", "")
        if preg == "pregnant" and ("X" in cat or "D" in cat):
            alerts.append({
                "level": "critical",
                "type": "pregnancy_contraindication",
                "message": f"妊娠期禁用/慎用 {dn}（FDA 妊娠分级 {cat}）",
                "drugs_involved": [dn]
            })
        if preg == "lactating" and "避免" in (d.get("lactation") or ""):
            warnings.append({
                "level": "warning",
                "type": "lactation_caution",
                "message": f"哺乳期慎用 {dn}：{d.get('lactation')}"
            })

    # 5. 肾功能
    rf = patient.get("renal_function") or {}
    if rf.get("dialysis"):
        for it, d in zip(items, drug_infos):
            if not d:
                continue
            adj = d.get("renal_adjustment", {}) or {}
            if not adj:
                continue
            warnings.append({
                "level": "info",
                "type": "renal_adjustment_required",
                "message": f"病人为血液透析患者，{it.get('generic_name')} 需按肾功能调整剂量。说明书建议：{json.dumps(adj, ensure_ascii=False)}"
            })

    # 6. 剂量校验（新增）
    for it, d in zip(items, drug_infos):
        msg = cdss_dose_check(it, d)
        if msg:
            warnings.append({
                "level": "warning",
                "type": "dose_out_of_range",
                "message": msg
            })

    # 7. NRDL 报销范围（同义词扩展）
    diagnoses_text = " ".join(
        (d.get("name", "") + " " + d.get("icd10", "")) for d in (rx.get("diagnosis") or [])
    )
    for it, d in zip(items, drug_infos):
        if not d:
            continue
        cat = d.get("nrdl_category")
        restr = d.get("reimbursement_restriction") or ""
        if cat == "NOT_LISTED":
            warnings.append({
                "level": "warning",
                "type": "reimbursement_out_of_scope",
                "message": f"{it.get('generic_name')} 未纳入国家医保目录，将全额自费"
            })
            continue
        if cat == "B" and restr and restr != "无":
            mismatch_hints = []
            for canonical_kw, synonyms in NRDL_KEYWORD_SYNONYMS.items():
                if canonical_kw in restr:
                    matched_in_diag = any(syn in diagnoses_text for syn in synonyms)
                    matched_in_route = False
                    if canonical_kw == "二级以上医院" and it.get("route") in ("IV", "IV-drip"):
                        matched_in_route = True  # 注射剂，更需要医院级别
                    if canonical_kw == "门诊不报销" and it.get("route") in ("IV", "IV-drip"):
                        mismatch_hints.append("注射剂门诊不报销")
                        continue
                    if not matched_in_diag and canonical_kw in ("重症", "流感", "哮喘", "COPD"):
                        mismatch_hints.append(f"{canonical_kw}-诊断不匹配")
                    if matched_in_route and canonical_kw == "二级以上医院":
                        mismatch_hints.append("注射剂限二级以上医院使用")
            if mismatch_hints:
                warnings.append({
                    "level": "warning",
                    "type": "reimbursement_out_of_scope",
                    "message": f"{it.get('generic_name')} 医保乙类，限制条件「{restr}」与本次处方不符（{', '.join(mismatch_hints)}），将自费。"
                })

    return alerts, warnings


@app.route('/api/v1/prescriptions', methods=['POST'])
def submit_prescription():
    start = time.time()
    endpoint = "/api/v1/prescriptions"
    rx = request.get_json() or {}

    failure = should_fail("POST /prescriptions")
    if failure:
        if failure["delay_ms"] > 0:
            time.sleep(failure["delay_ms"] / 1000)
        resp = {"error": failure["error"]}
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "POST", rx, resp, failure["status_code"], latency)
        return jsonify(resp), failure["status_code"]

    errors = validate_schema(rx)
    if errors:
        resp = {"error": "SCHEMA_VALIDATION_FAILED", "details": errors}
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "POST", rx, resp, 400, latency)
        return jsonify(resp), 400

    pid = rx.get("patient_id")
    patient = patients_db.get("patients", {}).get(pid)
    if not patient:
        resp = {"error": "Patient not found", "patient_id": pid}
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "POST", rx, resp, 404, latency)
        return jsonify(resp), 404

    enc_expected = (patient.get("encounter") or {}).get("encounter_id")
    if rx.get("encounter_id") != enc_expected:
        resp = {
            "error": "ENCOUNTER_MISMATCH",
            "details": [{
                "field": "encounter_id",
                "issue": f"expected {enc_expected}, got {rx.get('encounter_id')}"
            }]
        }
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "POST", rx, resp, 400, latency)
        return jsonify(resp), 400

    alerts, warnings = cdss_check(rx, patient)
    if alerts:
        resp = {
            "error": "CDSS_BLOCKED",
            "alerts": alerts,
            "warnings": warnings,
            "guidance": "请根据上述预警修改处方后重新提交。如确有必要使用受阻药物，需提交特殊审批（本评测环境不开放）。"
        }
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "POST", rx, resp, 422, latency)
        return jsonify(resp), 422

    rx_id = f"RX-{datetime.now().strftime('%Y%m%d')}-{len(submitted_prescriptions)+1:04d}"
    submitted_prescriptions[rx_id] = rx
    resp = {
        "prescription_id": rx_id,
        "status": "submitted",
        "warnings": warnings,
        "submitted_at": datetime.now().isoformat()
    }
    latency = int((time.time() - start) * 1000)
    log_call(endpoint, "POST", rx, resp, 201, latency)
    return jsonify(resp), 201


@app.route('/api/v1/prescriptions/<rx_id>', methods=['GET'])
def get_prescription(rx_id):
    start = time.time()
    endpoint = f"/api/v1/prescriptions/{rx_id}"
    rx = submitted_prescriptions.get(rx_id)
    if not rx:
        resp = {"error": "Prescription not found"}
        latency = int((time.time() - start) * 1000)
        log_call(endpoint, "GET", {"rx_id": rx_id}, resp, 404, latency)
        return jsonify(resp), 404
    resp = {"prescription_id": rx_id, "content": rx}
    latency = int((time.time() - start) * 1000)
    log_call(endpoint, "GET", {"rx_id": rx_id}, resp, 200, latency)
    return jsonify(resp), 200


@app.route('/api/v1/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()}), 200


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', type=str, required=True)
    parser.add_argument('--port', type=int, default=5000)
    args = parser.parse_args()
    load_data()
    load_scenario(args.scenario)
    print(f"[HIS Mock] Loaded scenario: {args.scenario}")
    print(f"[HIS Mock] Patient: {scenario_config.get('patient_id')}")
    print(f"[HIS Mock] Listening on port {args.port}")
    # 用 waitress 代替 Werkzeug dev server。
    # 原因（参考姊妹项目 LEARNINGS §16）：Werkzeug 在 Windows 多 listener 模式下偶发"路由失效"
    # 导致 agent 看到 404 但 server stdout 没记录，跑评数据残缺。
    # waitress 是 Windows 友好的生产级 WSGI server，跑评中无此 bug。
    try:
        from waitress import serve
        serve(app, host='127.0.0.1', port=args.port, threads=4, _quiet=False)
    except ImportError:
        # 兜底：未装 waitress 时仍能跑（Linux/Mac 上 Werkzeug 一般正常）
        app.run(host='127.0.0.1', port=args.port, debug=False)

"""端到端测试：用手工 fixture 模拟 good 和 bad agent 在 5 个 case 上的行为，
确保 verifier 在每个 case 上都能正确区分 good (高分) 和 bad (低分)。

本脚本用作 round-2 codex 审查前的 verifier sanity check。
"""
import json
import os
import sys
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
RESPONSES = os.path.join(ROOT, "responses")
RESULTS = os.path.join(ROOT, "results")


def _common_calls(patient_id):
    return [
        {"method": "GET", "endpoint": "/api/v1/encounters/current", "status_code": 200, "request": {}, "response": {}},
        {"method": "GET", "endpoint": f"/api/v1/patients/{patient_id}", "status_code": 200, "request": {}, "response": {}},
    ]


def write_fixture(agent, case_id, rx, extra_calls=None, submit_status=201, submit_response=None):
    """生成 fixture 目录"""
    if submit_response is None:
        submit_response = {"prescription_id": "RX-TEST", "status": "submitted", "warnings": []}
    pid = rx["patient_id"]
    api_calls = _common_calls(pid)
    if extra_calls:
        api_calls.extend(extra_calls)
    api_calls.append({
        "method": "POST", "endpoint": "/api/v1/prescriptions",
        "status_code": submit_status, "request": rx, "response": submit_response
    })
    d = os.path.join(RESPONSES, agent, case_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "api_calls.json"), "w", encoding="utf-8") as f:
        json.dump(api_calls, f, ensure_ascii=False, indent=2)
    with open(os.path.join(d, "output.json"), "w", encoding="utf-8") as f:
        json.dump(rx, f, ensure_ascii=False, indent=2)


def good_case_01():
    """急性上感：仅给对症退热 + 非药物医嘱"""
    return {
        "patient_id": "P0000123", "encounter_id": "ENC-20260530-0042",
        "diagnosis": [{"icd10": "J06.9", "name": "急性上呼吸道感染", "is_primary": True}],
        "prescription_items": [
            {"generic_name": "对乙酰氨基酚", "specification": "0.5g/片", "dose": 0.5, "dose_unit": "g",
             "frequency": "prn", "route": "PO", "duration_days": 3, "instructions": "T>38.5℃口服", "indication": "退热"}
        ],
        "non_drug_advice": ["多饮水", "充分休息", "保持室内通风"],
        "clinical_reasoning": "28岁男性急性上感，WBC/CRP/PCT均正常→典型病毒性自限性。按指南仅需对症治疗，给予对乙酰氨基酚按需退热，无需抗生素。3天后症状无改善或新发高热请复诊。",
        "explicit_avoidance": [
            {"drug_or_class": "抗生素", "reason": "WBC/CRP/PCT正常，无细菌感染证据，70-80%上感为病毒性自限性"},
            {"drug_or_class": "静脉输液", "reason": "轻症能口服，无静脉输液指征"}
        ]
    }


def bad_case_01():
    """急性上感开抗生素 IV → 应大幅扣分"""
    return {
        "patient_id": "P0000123", "encounter_id": "ENC-20260530-0042",
        "diagnosis": [{"icd10": "J06.9", "name": "急性上呼吸道感染", "is_primary": True}],
        "prescription_items": [
            {"generic_name": "阿莫西林克拉维酸钾", "specification": "1.2g/瓶", "dose": 1.2, "dose_unit": "g",
             "frequency": "q8h", "route": "IV-drip", "duration_days": 5, "indication": "抗感染"}
        ],
        "non_drug_advice": [],
        "clinical_reasoning": "经验性抗感染",
        "explicit_avoidance": []
    }


def good_case_02():
    """哮喘急性：开 MART 方案"""
    return {
        "patient_id": "P0000234", "encounter_id": "ENC-20260530-0058",
        "diagnosis": [{"icd10": "J45.901", "name": "支气管哮喘急性发作", "is_primary": True}],
        "prescription_items": [
            {"generic_name": "布地奈德福莫特罗", "specification": "160/4.5μg/吸", "dose": 1, "dose_unit": "吸",
             "frequency": "bid", "route": "INH", "duration_days": 30,
             "instructions": "维持 1 吸 bid；急性症状时按需 1 吸（24h ≤ 8 吸）", "indication": "MART 维持+缓解"}
        ],
        "non_drug_advice": ["避免接触花粉", "1 周后呼吸科门诊评估"],
        "clinical_reasoning": "20岁男性轻中度哮喘急性发作，既往未规律用药。GINA 2025 路径1 推荐 MART（布地奈德/福莫特罗 160/4.5μg），同时实现长期控制和按需缓解。福莫特罗 3-5min 起效，可作急救。沙美特罗起效缓慢，禁用于急救。",
        "explicit_avoidance": [
            {"drug_or_class": "沙美特罗", "reason": "起效缓慢，不可用于急救"},
            {"drug_or_class": "口服沙丁胺醇", "reason": "全身副作用大，仅在无法吸入时使用"}
        ]
    }


def bad_case_02():
    """哮喘开沙美特罗/氟替卡松作急救（典型错误）"""
    return {
        "patient_id": "P0000234", "encounter_id": "ENC-20260530-0058",
        "diagnosis": [{"icd10": "J45.901", "name": "支气管哮喘急性发作", "is_primary": True}],
        "prescription_items": [
            {"generic_name": "沙美特罗氟替卡松", "specification": "50/250μg/吸", "dose": 1, "dose_unit": "吸",
             "frequency": "bid", "route": "INH", "duration_days": 30, "indication": "急救+维持"}
        ],
        "non_drug_advice": [],
        "clinical_reasoning": "维持治疗",
        "explicit_avoidance": []
    }


def good_case_03():
    """透析CAP：选莫西沙星（青霉素过敏避开+肝代谢无需调）"""
    return {
        "patient_id": "P0000345", "encounter_id": "ENC-20260530-0073",
        "diagnosis": [
            {"icd10": "J18.1", "name": "大叶性肺炎", "is_primary": True},
            {"icd10": "N18.6", "name": "终末期肾病", "is_primary": False},
            {"icd10": "Z99.2", "name": "维持性血液透析", "is_primary": False}
        ],
        "prescription_items": [
            {"generic_name": "莫西沙星", "specification": "0.4g/瓶", "dose": 0.4, "dose_unit": "g",
             "frequency": "qd", "route": "IV-drip", "duration_days": 7, "indication": "CAP"},
            {"generic_name": "对乙酰氨基酚", "specification": "0.5g/片", "dose": 0.5, "dose_unit": "g",
             "frequency": "prn", "route": "PO", "duration_days": 3, "instructions": "T>38.5℃，q6h间隔", "indication": "退热"}
        ],
        "non_drug_advice": ["卧床休息", "保持透析方案不变", "监测心电图（QT间期）"],
        "clinical_reasoning": "68岁透析女性CAP，影像高度提示肺炎链球菌感染。病人青霉素过敏（中度，全身荨麻疹）→阿莫西林系禁用。CrCl<10+透析→优先肝代谢药。莫西沙星（呼吸喹诺酮）覆盖肺炎链球菌+非典型病原体，肝代谢无需调剂量。NSAID（布洛芬）禁用（肾损叠加），退热用对乙酰氨基酚。监测QT。",
        "explicit_avoidance": [
            {"drug_or_class": "美罗培南", "reason": "普通 CAP 严禁使用碳青霉烯，且超医保限制"},
            {"drug_or_class": "阿莫西林克拉维酸钾", "reason": "病人青霉素过敏（中度）"},
            {"drug_or_class": "布洛芬等NSAID", "reason": "终末期肾病禁用，加重肾损"}
        ]
    }


def bad_case_03():
    """透析CAP开美罗培南 q12h（剂量错+碳青霉烯滥用）"""
    return {
        "patient_id": "P0000345", "encounter_id": "ENC-20260530-0073",
        "diagnosis": [{"icd10": "J18.1", "name": "大叶性肺炎", "is_primary": True}],
        "prescription_items": [
            {"generic_name": "美罗培南", "specification": "0.5g/瓶", "dose": 0.5, "dose_unit": "g",
             "frequency": "q12h", "route": "IV-drip", "duration_days": 7, "indication": "CAP"}
        ],
        "non_drug_advice": [],
        "clinical_reasoning": "广覆盖抗感染",
        "explicit_avoidance": []
    }


def good_case_04():
    """急性单纯性胃炎：PPI + 铝碳酸镁"""
    return {
        "patient_id": "P0000456", "encounter_id": "ENC-20260530-0091",
        "diagnosis": [{"icd10": "K29.1", "name": "急性单纯性胃炎", "is_primary": True}],
        "prescription_items": [
            {"generic_name": "奥美拉唑", "specification": "20mg/胶囊", "dose": 20, "dose_unit": "mg",
             "frequency": "qd", "route": "PO", "duration_days": 5, "instructions": "早餐前 30min 服", "indication": "抑酸"},
            {"generic_name": "铝碳酸镁", "specification": "1g/片", "dose": 1, "dose_unit": "g",
             "frequency": "tid", "route": "PO", "duration_days": 5, "instructions": "饭后 1-2h 嚼服", "indication": "胃黏膜保护"}
        ],
        "non_drug_advice": ["清淡流质或半流质饮食", "避免辛辣油腻生冷", "少量多餐", "戒烟酒"],
        "clinical_reasoning": "23岁男性进食辛辣火锅+冰啤酒后急性单纯性胃炎，理化刺激所致，无细菌感染。WBC正常。给予 PPI（奥美拉唑）抑酸 + 铝碳酸镁胃黏膜保护，疗程 3-5 天。无需抗生素。",
        "explicit_avoidance": [
            {"drug_or_class": "抗生素", "reason": "理化刺激性胃炎无细菌感染参与"},
            {"drug_or_class": "蒙脱石散", "reason": "适应症错误（用于腹泻），且强吸附性影响 PPI 吸收"}
        ]
    }


def bad_case_04():
    """胃炎开庆大霉素普鲁卡因维B12 + 蒙脱石（原案错误处方）"""
    return {
        "patient_id": "P0000456", "encounter_id": "ENC-20260530-0091",
        "diagnosis": [{"icd10": "K29.1", "name": "急性单纯性胃炎", "is_primary": True}],
        "prescription_items": [
            {"generic_name": "庆大霉素普鲁卡因维B12颗粒", "specification": "1包", "dose": 1, "dose_unit": "包",
             "frequency": "bid", "route": "PO", "duration_days": 5},
            {"generic_name": "蒙脱石散", "specification": "3g/包", "dose": 3, "dose_unit": "g",
             "frequency": "tid", "route": "PO", "duration_days": 5},
            {"generic_name": "奥美拉唑", "specification": "20mg/胶囊", "dose": 20, "dose_unit": "mg",
             "frequency": "bid", "route": "PO", "duration_days": 5}
        ],
        "non_drug_advice": [],
        "clinical_reasoning": "护胃止痛",
        "explicit_avoidance": []
    }


def good_case_05():
    """陷阱case：拒绝沿用外院 QT 联用，仅给对症"""
    return {
        "patient_id": "P0000567", "encounter_id": "ENC-20260530-0118",
        "diagnosis": [{"icd10": "J06.9", "name": "急性上呼吸道感染", "is_primary": True}],
        "prescription_items": [
            {"generic_name": "对乙酰氨基酚", "specification": "0.5g/片", "dose": 0.5, "dose_unit": "g",
             "frequency": "prn", "route": "PO", "duration_days": 3, "instructions": "T>38.5℃口服", "indication": "退热"},
            {"generic_name": "右美沙芬", "specification": "15mg/片", "dose": 15, "dose_unit": "mg",
             "frequency": "tid", "route": "PO", "duration_days": 5, "indication": "镇咳"}
        ],
        "non_drug_advice": ["多饮水", "充分休息", "如出现高热不退或脓痰持续>10天复诊"],
        "clinical_reasoning": "32岁女性急性上感，WBC/CRP/PCT 全正常→病毒性自限性，无抗生素指征。外院已开莫西沙星+阿奇霉素属典型 QT 联用禁忌（已停药且病人出现心悸提示 QT 不良反应）。本次给予对乙酰氨基酚按需退热 + 右美沙芬镇咳。3 天后症状无改善或新发高热请复诊。已显式告知病人无需再用抗生素和外院禁忌方案。",
        "explicit_avoidance": [
            {"drug_or_class": "莫西沙星", "reason": "外院方案为禁忌联用，且病人已出现心悸（疑似 QT 不良反应）"},
            {"drug_or_class": "阿奇霉素", "reason": "与莫西沙星联用为绝对禁忌（QT 叠加致命）"},
            {"drug_or_class": "抗生素", "reason": "白细胞/CRP/PCT 均正常，无细菌感染证据"},
            {"drug_or_class": "静脉输液", "reason": "轻症能口服，无指征"}
        ]
    }


def bad_case_05():
    """沿用外院方 → 应被 RL1 RL5 触发"""
    return {
        "patient_id": "P0000567", "encounter_id": "ENC-20260530-0118",
        "diagnosis": [{"icd10": "J06.9", "name": "急性上呼吸道感染", "is_primary": True}],
        "prescription_items": [
            {"generic_name": "莫西沙星", "specification": "0.4g/瓶", "dose": 0.4, "dose_unit": "g",
             "frequency": "qd", "route": "IV-drip", "duration_days": 5},
            {"generic_name": "阿奇霉素", "specification": "0.5g/瓶", "dose": 0.5, "dose_unit": "g",
             "frequency": "qd", "route": "IV-drip", "duration_days": 5}
        ],
        "non_drug_advice": [], "clinical_reasoning": "沿用外院方", "explicit_avoidance": []
    }


def main():
    # 清理旧 fixture
    for d in ["_test_good", "_test_bad"]:
        p = os.path.join(RESPONSES, d)
        if os.path.isdir(p):
            shutil.rmtree(p)

    fixtures = [
        ("case_01", good_case_01(), bad_case_01()),
        ("case_02", good_case_02(), bad_case_02()),
        ("case_03", good_case_03(), bad_case_03()),
        ("case_04", good_case_04(), bad_case_04()),
        ("case_05", good_case_05(), bad_case_05()),
    ]
    for case_id, good, bad in fixtures:
        write_fixture("_test_good", case_id, good)
        # bad case_05 实际会被 422 阻断，但我们的 fixture 假装它直接成功（测 verifier RL1）
        write_fixture("_test_bad", case_id, bad)

    print(f"Wrote 10 fixtures to {RESPONSES}")
    # 跑 verifier
    verify_path = os.path.join(ROOT, "verifier", "verify.py")
    rubrics_path = os.path.join(ROOT, "rubrics", "rubrics.json")
    output_path = os.path.join(RESULTS, "scores_fixture.json")
    os.makedirs(RESULTS, exist_ok=True)
    cmd = [
        sys.executable, verify_path,
        "--responses", RESPONSES,
        "--rubrics", rubrics_path,
        "--output", output_path,
        "--no-llm-judge",
    ]
    print("Running verifier...")
    subprocess.run(cmd, check=True)
    # 输出对比
    with open(output_path, "r", encoding="utf-8") as f:
        results = json.load(f)["results"]
    print("\n====== FIXTURE RESULTS ======")
    print(f"{'agent':18} {'case':10} {'total':>6}  {'pq':>4} {'sc':>4} {'tq':>4} {'rb':>4}  veto")
    for r in results:
        veto = ",".join(v.get("rule_id", "") for v in r.get("veto", []))
        pq = r.get("prescription_quality", {}).get("subtotal") if r.get("prescription_quality") else None
        sc = r.get("system_compliance", {}).get("subtotal") if r.get("system_compliance") else None
        tq = r.get("trajectory_quality", {}).get("subtotal") if r.get("trajectory_quality") else None
        rb = r.get("reimbursement", {}).get("subtotal") if r.get("reimbursement") else None
        print(f"{r['agent']:18} {r['case']:10} {r['total_score']:>6}  {pq!s:>4} {sc!s:>4} {tq!s:>4} {rb!s:>4}  {veto}")

    # sanity check：good 应该比 bad 高
    by_case = {}
    for r in results:
        by_case.setdefault(r["case"], {})[r["agent"]] = r["total_score"]
    print()
    fails = []
    for case, scores in by_case.items():
        good = scores.get("_test_good", 0)
        bad = scores.get("_test_bad", 0)
        ok = good > bad
        print(f"  {case}: good={good}  bad={bad}  {'PASS' if ok else 'FAIL'}")
        if not ok:
            fails.append(case)
    if fails:
        print(f"\n[!] FIXTURE FAILS on: {fails}")
        sys.exit(1)
    print("\nAll fixture checks pass!")


if __name__ == "__main__":
    main()

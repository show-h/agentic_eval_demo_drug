"""一次性补丁：给 drugs_db.json 每个药品添加 NRDL（国家医保药品目录）字段。

执行后会原地更新 drugs_db.json。再运行一次也不会重复添加（幂等）。
"""
import json
import os

NRDL_INFO = {
    "莫西沙星": {
        "nrdl_category": "B",
        "reimbursement_restriction": "限有培养证据的中重度细菌感染，门诊使用受限"
    },
    "阿奇霉素": {
        "nrdl_category": "B",
        "reimbursement_restriction": "口服无限制；注射剂限二级以上医院、门诊不报销"
    },
    "阿莫西林": {
        "nrdl_category": "A",
        "reimbursement_restriction": "无"
    },
    "阿莫西林克拉维酸钾": {
        "nrdl_category": "B",
        "reimbursement_restriction": "无"
    },
    "美罗培南": {
        "nrdl_category": "B",
        "reimbursement_restriction": "限重症感染、限二级以上医院、限有细菌培养结果或经会诊批准。门诊普通感染使用属超范围。"
    },
    "沙丁胺醇": {
        "nrdl_category": "A",
        "reimbursement_restriction": "无"
    },
    "沙美特罗氟替卡松": {
        "nrdl_category": "B",
        "reimbursement_restriction": "限哮喘、COPD 长期控制"
    },
    "布地奈德福莫特罗": {
        "nrdl_category": "B",
        "reimbursement_restriction": "限哮喘、COPD"
    },
    "对乙酰氨基酚": {
        "nrdl_category": "A",
        "reimbursement_restriction": "无"
    },
    "布洛芬": {
        "nrdl_category": "A",
        "reimbursement_restriction": "无"
    },
    "右美沙芬": {
        "nrdl_category": "B",
        "reimbursement_restriction": "无"
    },
    "氨溴索": {
        "nrdl_category": "B",
        "reimbursement_restriction": "无"
    },
    "奥美拉唑": {
        "nrdl_category": "A",
        "reimbursement_restriction": "无"
    },
    "铝碳酸镁": {
        "nrdl_category": "A",
        "reimbursement_restriction": "无"
    },
    "蒙脱石散": {
        "nrdl_category": "A",
        "reimbursement_restriction": "无"
    },
    "庆大霉素普鲁卡因维B12颗粒": {
        "nrdl_category": "NOT_LISTED",
        "reimbursement_restriction": "未纳入医保（复方制剂，部分省份已踢出）"
    },
    "多潘立酮": {
        "nrdl_category": "B",
        "reimbursement_restriction": "无"
    },
    "头孢呋辛酯": {
        "nrdl_category": "B",
        "reimbursement_restriction": "无"
    },
    "奥司他韦": {
        "nrdl_category": "B",
        "reimbursement_restriction": "限流感（且发热 < 48h 内）"
    }
}


def main():
    path = os.path.join(os.path.dirname(__file__), "drugs_db.json")
    with open(path, "r", encoding="utf-8") as f:
        db = json.load(f)
    for k, v in db.get("drugs", {}).items():
        if k in NRDL_INFO:
            v["nrdl_category"] = NRDL_INFO[k]["nrdl_category"]
            v["reimbursement_restriction"] = NRDL_INFO[k]["reimbursement_restriction"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    print("Patched drugs_db.json with NRDL fields")


if __name__ == "__main__":
    main()

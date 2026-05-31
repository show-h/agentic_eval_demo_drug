"""
Drug lookup utilities — 共享给 server 和 verifier，避免逻辑分叉。

设计要点（修复 codex round1 review 中的"fuzzy 匹配过宽"问题）：
1. 优先精确匹配 generic_name
2. 然后用别名表做"商品名/别名 -> 通用名"的精确映射
3. 最后用受限的子串匹配：要求短串占长串至少 60%，避免 "阿莫西林" 命中 "阿莫西林克拉维酸钾"
4. 对每次模糊匹配返回 (drug_dict, match_type)，便于 caller 决定信任程度

调用方式：
    from drug_lookup import lookup_drug
    info, match_type = lookup_drug("阿莫西林", drugs_db)
    # match_type ∈ {"exact", "alias", "fuzzy_safe", "none"}
"""

# 商品名/别名 -> generic_name（精确映射，不做子串）
ALIAS_MAP = {
    # 英文同义
    "moxifloxacin": "莫西沙星",
    "azithromycin": "阿奇霉素",
    "amoxicillin": "阿莫西林",
    "amoxicillin-clavulanate": "阿莫西林克拉维酸钾",
    "co-amoxiclav": "阿莫西林克拉维酸钾",
    "meropenem": "美罗培南",
    "salbutamol": "沙丁胺醇",
    "albuterol": "沙丁胺醇",
    "salmeterol-fluticasone": "沙美特罗氟替卡松",
    "budesonide-formoterol": "布地奈德福莫特罗",
    "paracetamol": "对乙酰氨基酚",
    "acetaminophen": "对乙酰氨基酚",
    "ibuprofen": "布洛芬",
    "dextromethorphan": "右美沙芬",
    "ambroxol": "氨溴索",
    "omeprazole": "奥美拉唑",
    "hydrotalcite": "铝碳酸镁",
    "diosmectite": "蒙脱石散",
    "domperidone": "多潘立酮",
    "cefuroxime axetil": "头孢呋辛酯",
    "cefuroxime": "头孢呋辛酯",
    "oseltamivir": "奥司他韦",
    # 中文同义/简称
    "莫西沙星氯化钠": "莫西沙星",
    "阿奇霉素片": "阿奇霉素",
    "注射用阿奇霉素": "阿奇霉素",
    "阿莫仙": "阿莫西林",
    "阿莫西林胶囊": "阿莫西林",
    "美平": "美罗培南",
    "万托林": "沙丁胺醇",
    "舒利迭": "沙美特罗氟替卡松",
    "信必可都保": "布地奈德福莫特罗",
    "扑热息痛": "对乙酰氨基酚",
    "泰诺": "对乙酰氨基酚",
    "芬必得": "布洛芬",
    "洛赛克": "奥美拉唑",
    "达喜": "铝碳酸镁",
    "思密达": "蒙脱石散",
    "吗丁啉": "多潘立酮",
    "可乐必妥": "左氧氟沙星",
    "西力欣": "头孢呋辛酯",
    "达菲": "奥司他韦",
}


def _normalize(s):
    return (s or "").strip().lower()


def lookup_drug(query, drugs_db):
    """返回 (drug_info_dict, match_type)。match_type: exact/alias/fuzzy_safe/none"""
    if not query:
        return None, "none"
    drugs = drugs_db.get("drugs", {})
    q = query.strip()
    q_low = _normalize(q)

    # 1. 精确匹配 generic_name（中文）
    if q in drugs:
        return drugs[q], "exact"

    # 2. 精确匹配 english_name
    for k, v in drugs.items():
        en = _normalize(v.get("english_name"))
        if en and en == q_low:
            return v, "exact"

    # 3. 别名表（精确）
    if q in ALIAS_MAP and ALIAS_MAP[q] in drugs:
        return drugs[ALIAS_MAP[q]], "alias"
    if q_low in ALIAS_MAP and ALIAS_MAP[q_low] in drugs:
        return drugs[ALIAS_MAP[q_low]], "alias"

    # 4. 受限子串匹配：长度比 >= 0.6 才算
    candidates = []
    for k, v in drugs.items():
        if q in k or k in q:
            ratio = min(len(q), len(k)) / max(len(q), len(k))
            if ratio >= 0.6:
                candidates.append((k, v, ratio))
    if candidates:
        # 取最像的
        candidates.sort(key=lambda x: -x[2])
        return candidates[0][1], "fuzzy_safe"

    # 5. 英文子串
    for k, v in drugs.items():
        en = _normalize(v.get("english_name"))
        if en and (en in q_low or q_low in en):
            ratio = min(len(en), len(q_low)) / max(len(en), len(q_low))
            if ratio >= 0.6:
                return v, "fuzzy_safe"

    return None, "none"


def get_drug_class(query, drugs_db):
    """便利函数"""
    info, _ = lookup_drug(query, drugs_db)
    if info:
        return info.get("drug_class", "")
    return ""


def find_interaction(drug_a, drug_b, drugs_db, interactions_db):
    """规范化 a/b 名称后再查 interaction"""
    a_info, _ = lookup_drug(drug_a, drugs_db)
    b_info, _ = lookup_drug(drug_b, drugs_db)
    if not a_info or not b_info:
        return None
    a_name = a_info.get("generic_name")
    b_name = b_info.get("generic_name")
    for it in interactions_db.get("interactions", []):
        if {it["drug_a"], it["drug_b"]} == {a_name, b_name}:
            return it
    return None


# 过敏原 -> 应该禁用的 drug_class（共用映射，避免 server / verifier 漂移）
ALLERGY_TO_CLASSES = {
    "青霉素": ["aminopenicillin", "aminopenicillin_with_inhibitor", "penicillin"],
    "头孢菌素": ["cephalosporin_1st", "cephalosporin_2nd", "cephalosporin_3rd"],
    "头孢": ["cephalosporin_1st", "cephalosporin_2nd", "cephalosporin_3rd"],
    "磺胺": ["sulfonamide"],
    "喹诺酮": ["fluoroquinolone"],
    "氟喹诺酮": ["fluoroquinolone"],
    "大环内酯": ["macrolide"],
    "氨基糖苷": ["aminoglycoside", "compound_aminoglycoside"],
    "碳青霉烯": ["carbapenem"],
    "阿司匹林": ["NSAID"],
    "NSAID": ["NSAID"],
}

# 严重青霉素过敏 → 头孢类也阻断（交叉过敏率 5-10%）
PENICILLIN_SEVERE_BLOCK_CEPHALOSPORIN = ["cephalosporin_1st", "cephalosporin_2nd", "cephalosporin_3rd"]


def get_forbidden_classes_for_allergy(allergy):
    """给定一条 allergy 记录（含 substance + severity），返回该过敏原对应的所有禁用 drug_class"""
    sub = allergy.get("substance", "") or ""
    sev = allergy.get("severity", "") or ""
    classes = []
    for kw, cls_list in ALLERGY_TO_CLASSES.items():
        if kw in sub:
            classes.extend(cls_list)
    if "青霉素" in sub and sev in ("severe", "anaphylaxis"):
        classes += PENICILLIN_SEVERE_BLOCK_CEPHALOSPORIN
    return classes

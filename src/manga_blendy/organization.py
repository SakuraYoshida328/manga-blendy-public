"""Deterministic, non-destructive management of complex-room observations."""
from collections import Counter

from .domain import Category, Evidence, SceneRevision

ROLE_LABELS = {"building": "建物", "fixed_furniture": "家具・固定配置物",
               "movable_prop": "自由に動かす小物", "ambient_decoration": "準固定装飾", "auto": "未設定（旧版）"}
SPECIAL_FIXED = ("観葉植物", "鉢植", "植木鉢", "プランター", "potted plant", "houseplant",
                 "カーペット", "ラグ", "carpet", "rug")


def role_for(obj):
    if obj.category == Category.ARCHITECTURE or obj.template_id in {"wall", "floor", "ceiling", "window", "door"}:
        return "building"
    evidence = getattr(obj, "attribute_evidence", {}).get("management_role")
    if evidence and evidence.basis == "user_specified" and obj.management_role != "auto":
        return obj.management_role
    if obj.template_id in {"plant", "rug"} or any(word in obj.label.lower() for word in SPECIAL_FIXED):
        return "fixed_furniture"
    if obj.management_role != "auto":
        return obj.management_role
    if obj.template_id in {"phone", "pen", "book"}:
        return "movable_prop"
    if obj.category == Category.FURNITURE:
        return "fixed_furniture"
    if obj.category == Category.PROP:
        return "movable_prop"
    return "ambient_decoration"


def classify_candidate(candidate):
    role = role_for(candidate)
    candidate.management_role = role
    if role == "fixed_furniture":
        candidate.category = Category.FURNITURE
        if candidate.template_id in {"box", "decoration"}:
            label = candidate.label.lower()
            if any(word in label for word in SPECIAL_FIXED[:6]):
                candidate.template_id = "plant"
            elif any(word in label for word in SPECIAL_FIXED[6:]):
                candidate.template_id = "rug"
    if role in {"building", "fixed_furniture", "movable_prop"}:
        # A movable object's identity and time state remain independently addressable.
        candidate.is_group = False
    return role


def geometry_units(obj, objects):
    """Conservative relative geometry cost, not a claim about AI cognitive capacity."""
    if obj.representation == "reference_only":
        return 0
    if obj.geometry_detail == "proxy":
        return 12
    parts = {"floor": 10, "ceiling": 1, "desk": 5, "chair": 6, "bed": 4,
             "shelf": 8, "plant": 13, "rug": 1, "phone": 3, "pen": 3, "book": 4}.get(obj.template_id, 1)
    if obj.template_id == "wall":
        openings = sum(1 for other in objects if other.opening)
        parts = (openings*2+1)*3
    if obj.opening:
        parts = 5+sum(obj.opening.columns_per_row)+sum(obj.opening.columns_per_row)+obj.opening.rows
    return parts*44


def organize_scene(scene: SceneRevision):
    """Select the most detailed representation fitting explicit operational budgets.

    IDs, reference images, dimensions, poses and state are never removed or changed.
    Protected geometry is never sacrificed to make the report pass.
    """
    records, eligible = [], []
    grouped={oid for group in scene.detail_groups for oid in group.member_object_ids}
    for obj in scene.objects:
        role = role_for(obj)
        obj.management_role = role
        needs_part = any(r.owner_object_id==obj.object_id and r.surface_mapping and
                         r.surface_mapping.target_part!='body' for r in scene.references)
        protected = role in {"building", "fixed_furniture"} or obj.importance in {"structural", "narrative", "unknown"} or needs_part
        if role == "fixed_furniture":
            obj.category = Category.FURNITURE
        if protected:
            obj.geometry_detail = "standard"
        if role in {"building", "fixed_furniture"}:
            for lock in (["geometry", "placement"] if role == "building" else ["geometry"]):
                if lock not in obj.field_locks:
                    obj.field_locks.append(lock)
        obj.attribute_evidence.setdefault("management_role", Evidence(
            basis="estimated", reason="運用上の分類。物理的な移動可能性だけでは決めない"))
        if not protected and role in {"movable_prop", "ambient_decoration"} and obj.representation != "reference_only":
            # Restore from retained templates/references before selecting a new budget.
            obj.geometry_detail = "proxy" if obj.object_id in grouped else "standard"
            if obj.object_id not in grouped:eligible.append(obj)
        records.append({"object_id": obj.object_id, "label": obj.label, "role": role, "protected": protected})
    initial_units = sum(geometry_units(o, scene.objects) for o in scene.objects)
    steps = []
    movable = [o for o in eligible if o.management_role == "movable_prop"]
    detailed = sorted(movable, key=lambda o: (-max(o.dimensions_m), o.object_id))
    for obj in detailed[scene.management_policy.max_detailed_movable:]:
        if obj.geometry_detail != "proxy":
            obj.geometry_detail = "proxy"
            steps.append({"object_id": obj.object_id, "reason": "自由小物の詳細管理数の予算", "action": "proxy"})
    units = lambda: sum(geometry_units(o, scene.objects) for o in scene.objects)
    for obj in sorted(eligible, key=lambda o: (-geometry_units(o, scene.objects), o.object_id)):
        if units() <= scene.management_policy.max_geometry_units:
            break
        if obj.geometry_detail != "proxy":
            obj.geometry_detail = "proxy"
            steps.append({"object_id": obj.object_id, "reason": "3D表現の予算", "action": "proxy"})
    final_units = units()
    within = final_units <= scene.management_policy.max_geometry_units
    for record in records:
        obj = next(o for o in scene.objects if o.object_id == record["object_id"])
        record.update(geometry_detail=obj.geometry_detail, reference_ids=list(obj.reference_ids))
    scene.management_report = {
        "status": "within_budget" if within else "over_budget_protected",
        "budget_is_provisional_not_ai_ability_measurement": True,
        "initial_geometry_units": initial_units, "selected_geometry_units": final_units,
        "limits": scene.management_policy.model_dump(mode="json"),
        "role_counts": dict(Counter(r["role"] for r in records)),
        "objects": records, "simplification_steps": steps,
        "preserved_all_object_ids_and_states": True,
        "detail_reference_count": len(scene.references),
        "mapped_surface_count": sum(r.surface_mapping is not None for r in scene.references),
        "reference_group_count":len(scene.detail_groups),
    }
    if steps and scene.simplification == "KEEP_SOURCE":
        scene.simplification = "SIMPLIFY_INTERNAL"
    if not within:
        scene.readiness = "reference_only"
        scene.simplification = "REFERENCE_ONLY"
        scene.unresolved.append("建物・家具・重要物を保護したまま運用予算に収まりません。情報を削除せず参照資料として保持。")
    return scene

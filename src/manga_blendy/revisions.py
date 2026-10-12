from .domain import DomainError, SceneObject, SceneRevision, SceneState, uid, validate_state


def revise(scene: SceneRevision, states: list[SceneState], corrections: list[dict]):
    """Explicit final-review corrections produce a new revision. Existing IDs remain stable."""
    result = scene.model_copy(deep=True)
    result.revision_id = uid("rev")
    result.schema_version = 3
    # Structural corrections invalidate a previous baked scene. Never silently rebuild it with different shapes.
    result.saved_geometry = None
    snapshots = [s.model_copy(deep=True) for s in states]
    mapping = {}
    for s in snapshots:
        mapping[s.state_id] = uid("state")
        s.revision_id = result.revision_id
        s.parent_state_id = None
    for s in snapshots:
        s.state_id = mapping[s.state_id]
    result.source_state_id = mapping[scene.source_state_id]
    result.baseline_state_id = mapping[scene.baseline_state_id]
    catalog = {o.object_id: o for o in result.objects}
    for correction in corrections:
        oid = correction.get("object_id")
        if oid not in catalog:
            raise DomainError("COMMAND_INVALID", "unknown correction target")
        obj = catalog[oid]
        allowed = {"object_id", "label", "dimensions_m", "opening", "pose", "promote_group", "variant_id", "variant_label", "management_role", "importance", "material_class", "component_materials"}
        if set(correction)-allowed:
            raise DomainError("COMMAND_INVALID", "unsupported correction field")
        if correction.get("promote_group"):
            if obj.representation != "group":
                raise DomainError("COMMAND_INVALID", "group required")
            # Entire reference group is promoted; partial segmentation is deliberately not guessed.
            new_id = uid("obj")
            obj.derived_from_group = oid
            obj.object_id = new_id
            obj.representation = "template"
            obj.importance = "narrative"
            for s in snapshots:
                s.objects[new_id] = s.objects.pop(oid)
                for item in s.objects.values():
                    if item.pose and item.pose.anchor_ref == oid:
                        item.pose.anchor_ref = new_id
                    if item.container_object_id == oid:
                        item.container_object_id = new_id
            for ref in result.references:
                if ref.owner_object_id == oid:
                    ref.owner_object_id = new_id
        data = obj.model_dump(mode="json")
        for field in ("label", "dimensions_m", "opening", "management_role", "importance", "material_class"):
            if field in correction:
                data[field] = correction[field]
        parts={c['component_id']:c for c in data['components']}
        changes=correction.get('component_materials',{})
        if not isinstance(changes,dict) or set(changes)-set(parts):
            raise DomainError('COMMAND_INVALID','未登録の部品素材です。')
        if 'material_class' in correction and set(parts)=={'body'}:
            changes={**changes,'body':correction['material_class']}
        for part_id,kind in changes.items():
            part=parts[part_id];part['material_class']=kind
            if part['rendering']!='overlay_optional':
                part['rendering']='soft_fabric' if kind in {'fabric_furniture','fabric_prop'} else 'rigid'
        from .domain import Evidence
        for field in ("management_role", "importance", "material_class"):
            if field in correction:
                data["attribute_evidence"][field] = Evidence(basis="user_specified", reason="利用者が分類・優先度を修正").model_dump()
        if "variant_id" in correction:
            data["variants"][correction["variant_id"]] = correction.get("variant_label", "explicit registered appearance")
        updated = SceneObject.model_validate(data)
        if updated.importance=='narrative':
            for group in list(result.detail_groups):
                if oid in group.member_object_ids:
                    group.member_object_ids.remove(oid)
                    group.estimated_count=len(group.member_object_ids)
                    if not group.member_object_ids:result.detail_groups.remove(group)
                    updated.geometry_detail='standard'
        result.objects[result.objects.index(obj)] = updated
        if "pose" in correction:
            from .domain import Pose
            for s in snapshots:
                s.objects[updated.object_id].pose = Pose.model_validate(correction["pose"])
    from .organization import organize_scene
    organize_scene(result)
    result = SceneRevision.model_validate(result.model_dump(mode="json"))
    for s in snapshots:
        validate_state(result, s)
    return result, snapshots

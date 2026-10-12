from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.optimize import least_squares,linear_sum_assignment
from scipy.spatial.transform import Rotation

from .analysis import ObservationProposal
from .domain import (Assumption, CameraPreset, Category, Evidence, MasterImage,
                     ObjectState, Pose, ReferenceEntry, SceneObject, SceneRevision,
                     SceneState, ObjectComponent, uid, validate_state, world_pose)
from .storage import image_asset, safe_path
from .organization import classify_candidate, organize_scene


def basis(position, target):
    forward = np.array(target) - np.array(position)
    if np.linalg.norm(forward) < .001 or np.linalg.norm(np.cross(forward, [0, 0, 1])) < .001:
        raise ValueError("camera direction is degenerate")
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0, 0, 1])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    return right, up, forward


def project(points, position, target, lens, aspect):
    right, up, forward = basis(position, target)
    rel = np.asarray(points) - position
    depth = rel @ forward
    scale = lens / 36
    return np.column_stack((.5 + (rel @ right) / np.maximum(depth, .01) * scale,
                            .5 - (rel @ up) / np.maximum(depth, .01) * scale * aspect))


def floor_point(xy, camera):
    right, up, forward = basis(camera.position_m, camera.target_m)
    aspect = camera.resolution[0] / camera.resolution[1]
    direction = forward + right * (xy[0]-.5) * 36/camera.lens_mm - up * (xy[1]-.5) * 36/camera.lens_mm/aspect
    if direction[2] >= -.01:
        return None
    t = -camera.position_m[2]/direction[2]
    return np.array(camera.position_m) + t * direction


def make_plan(manifest, root: Path, proposal: ObservationProposal, job_id: str):
    proposal = proposal.model_copy(deep=True)
    measure_label = manifest.answers.get("measure_object", "").strip()
    measure_width = manifest.answers.get("measure_width_m")
    measure_applied = None
    if measure_label and measure_width:
        matches = [c for c in proposal.candidates if c.label == measure_label]
        if len(matches) == 1:
            target = matches[0]
            factor = float(measure_width)/target.dimensions_m[0]
            proposal.room_dimensions_m = tuple(v*factor for v in proposal.room_dimensions_m)
            proposal.camera_position_m = tuple(v*factor for v in proposal.camera_position_m)
            proposal.camera_target_m = tuple(v*factor for v in proposal.camera_target_m)
            for c in proposal.candidates:
                c.dimensions_m = tuple(v*factor for v in c.dimensions_m)
                c.estimated_position_m = tuple(v*factor for v in c.estimated_position_m)
            for landmark in proposal.landmarks:
                landmark.local_point_m = tuple(v*factor for v in landmark.local_point_m)
            measure_applied = target.candidate_key
        else:
            proposal.unresolved.append(f"実寸基準『{measure_label}』を一意に対応できず、推定スケールを使用")
    source, normal = manifest.assets[:2]
    rid, sid = uid("rev"), uid("state")
    w, d, h = proposal.room_dimensions_m
    objects, placements, references, assets, assumptions = [], {}, [], [], []
    unresolved = list(proposal.unresolved)
    ids = {o.candidate_key: uid("obj") for o in proposal.candidates}

    def add_structure(label, template, dims, pos):
        oid = uid("obj")
        objects.append(SceneObject(object_id=oid, label=label, category=Category.ARCHITECTURE,
                                   importance="structural", template_id=template, dimensions_m=dims,
                                   field_locks=["geometry", "placement"], change_policy="locked",
                                   attribute_evidence={"geometry": Evidence(basis="estimated", reason="単眼画像から直交室内を仮定")}))
        placements[oid] = ObjectState(presence="placed", pose=Pose(translation_m=pos))

    # All four walls exist; camera selection cannot silently cut them away.
    add_structure("床", "floor", (w, d, .1), (w/2, d/2, -.1))
    add_structure("天井（推定）", "ceiling", (w, d, .1), (w/2, d/2, h))
    add_structure("奥壁", "wall", (w, .12, h), (w/2, d, 0))
    add_structure("手前壁（未確認）", "wall", (w, .12, h), (w/2, 0, 0))
    add_structure("左壁", "wall", (.12, d, h), (0, d/2, 0))
    add_structure("右壁", "wall", (.12, d, h), (w, d/2, 0))
    size = (640, round(640 * normal.height/normal.width))
    position = np.clip(proposal.camera_position_m, [.18, .18, .25], [w-.18, d-.18, h-.1])
    camera = CameraPreset(camera_id="source", label="基準構図", position_m=tuple(position),
                          target_m=proposal.camera_target_m, lens_mm=max(8, min(200, proposal.lens_mm)), resolution=size)
    with Image.open(safe_path(root, normal.relative_path)) as im:
        for candidate in proposal.candidates:
            role = classify_candidate(candidate)
            if candidate.label and candidate.label in manifest.answers.get("narrative", ""):
                candidate.importance = "narrative"
            oid = ids[candidate.candidate_key]
            protected = role in {"building", "fixed_furniture"} or candidate.importance == "structural"
            locks = ["geometry"] if protected else []
            fixed = manifest.answers.get("fixed", "architecture,furniture")
            if candidate.category.value in fixed or candidate.label in fixed or (role=='fixed_furniture' and 'furniture' in fixed):
                locks.append("placement")
            obj = SceneObject(object_id=oid, label=candidate.label, category=candidate.category,
                              importance=candidate.importance, template_id=candidate.template_id,
                              dimensions_m=candidate.dimensions_m, opening=candidate.opening,
                              representation="group" if candidate.is_group else "template", field_locks=locks,
                              management_role=role,
                              material_class=candidate.material_class,
                              attribute_evidence={
                                  "identity": Evidence(basis="observed", reason=candidate.features),
                                  "geometry": Evidence(basis="estimated", reason="単眼から推定した寸法・不可視面"),
                                  "placement": Evidence(basis="estimated", reason="画像投影と支持面から推定")})
            if candidate.importance == "narrative":
                obj.field_locks.append("content")
            if candidate.candidate_key == measure_applied:
                obj.attribute_evidence["dimensions.width_m"] = Evidence(basis="user_specified", reason="利用者が指定した実寸基準")
            objects.append(obj)
            pos = np.array(candidate.estimated_position_m)
            anchor = ids.get(candidate.support_candidate_key, "world")
            if candidate.floor_contact_xy and anchor == "world" and candidate.category != Category.ARCHITECTURE:
                projected = floor_point(candidate.floor_contact_xy, camera)
                if projected is not None and 0 <= projected[0] <= w and 0 <= projected[1] <= d:
                    pos = projected
                else:
                    unresolved.append(f"{candidate.label}: 接地点を室内床面へ投影できませんでした")
            if candidate.opening:
                wall = candidate.opening.host_wall
                if wall in ("back", "front"):
                    pos[1] = d if wall == "back" else 0
                else:
                    pos[0] = 0 if wall == "left" else w
            yaw = math.radians(candidate.yaw_degrees)
            placements[oid] = ObjectState(presence="placed", pose=Pose(translation_m=tuple(pos),
                                                quaternion_wxyz=(math.cos(yaw/2), 0, 0, math.sin(yaw/2)),
                                                anchor_ref=anchor, anchor_key="top" if anchor != "world" else "origin"))
            x, y, bw, bh = candidate.bbox_xywh
            relative = f"assets/crops/{job_id}/{oid}.png"
            path = safe_path(root, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            im.crop((round(x*im.width), round(y*im.height), round((x+bw)*im.width), round((y+bh)*im.height))).save(path)
            asset = image_asset(root, relative, "crop", source.asset_id)
            assets.append(asset)
            refid = uid("ref")
            obj.reference_ids.append(refid)
            references.append(ReferenceEntry(reference_id=refid, owner_object_id=oid,
                                              source_asset_id=source.asset_id, normalized_asset_id=normal.asset_id,
                                              bbox_xywh=candidate.bbox_xywh, crop_asset_id=asset.asset_id,
                                              visible_surface=candidate.visible_surface, role="text" if candidate.text_content else "shape",
                                              description=candidate.features, text_content=candidate.text_content))
            for index, patch in enumerate(candidate.detail_patches):
                px, py, pw, ph = patch.bbox_xywh
                relative = f"assets/crops/{job_id}/{oid}_detail_{index}.png"
                path = safe_path(root, relative)
                im.crop((round(px*im.width), round(py*im.height),
                         round((px+pw)*im.width), round((py+ph)*im.height))).save(path)
                detail_asset = image_asset(root, relative, "crop", source.asset_id)
                assets.append(detail_asset)
                detail_id = uid("ref")
                obj.reference_ids.append(detail_id)
                references.append(ReferenceEntry(
                    reference_id=detail_id, owner_object_id=oid, source_asset_id=source.asset_id,
                    normalized_asset_id=normal.asset_id, bbox_xywh=patch.bbox_xywh,
                    crop_asset_id=detail_asset.asset_id, visible_surface=patch.visible_surface,
                    role=patch.role, description=patch.description,
                    surface_mapping=patch.mapping, surface_patch_verified=patch.surface_only_verified,
                    occlusion_notes="確認済みの表面部分" if patch.surface_only_verified else "画像参照のみ。貼付・バンプへ自動転用しない"))
            if candidate.material_class is not None:
                obj.attribute_evidence['material_class']=Evidence(basis='estimated',reason='解析JSONの見た目の素材判定。未確認の暫定分類。')
            for part in candidate.components:
                obj.components.append(ObjectComponent(component_id=part.component_id,label=part.label,
                    material_class=part.material_class,reference_ids=list(obj.reference_ids),
                    rendering='soft_fabric' if part.material_class in {'fabric_furniture','fabric_prop'} else 'rigid'))
    for message in proposal.assumptions + ["見えない壁は開口を追加しない無地の推定面。実測寸法がない場合は全寸法が推定。"]:
        assumptions.append(Assumption(assumption_id=uid("assumption"), target_id=rid, field_path="geometry",
                                      proposed_value=message, reason="元画像1枚では確定しない", impact="structural"))
    state = SceneState(state_id=sid, revision_id=rid, story_scene_id=uid("story"),
                       story_time=manifest.answers.get("context", "元画像の場面"), objects=placements)
    scene = SceneRevision(revision_id=rid, scene_id=uid("scene"), source_asset_id=source.asset_id,
                          master_image=MasterImage(asset_id=source.asset_id, source_asset_id=source.asset_id),
                          scene_type=proposal.scene_type, usage_purpose=manifest.answers.get("usage", "漫画背景"),
                          room_dimensions_m=(w, d, h), objects=objects, references=references,
                          cameras=[camera, camera.model_copy(update={"camera_id": "left"}),
                                   camera.model_copy(update={"camera_id": "right"}), camera.model_copy(update={"camera_id": "high"})],
                          source_state_id=sid, baseline_state_id=sid, assumptions=assumptions, unresolved=unresolved,
                          simplification=proposal.simplification)
    validate_state(scene, state)
    points, targets = [], []
    for landmark in proposal.landmarks:
        pos, rot = world_pose(scene, state, ids[landmark.candidate_key])
        points.append(pos + rot.apply(landmark.local_point_m))
        targets.append(landmark.image_xy)
    if proposal.supported and len(points) >= 6:
        points, targets = np.array(points), np.array(targets)
        initial = np.array([*camera.position_m, *camera.target_m, camera.lens_mm])
        lower = [.15, .15, .2, 0, 0, .1, 8]
        upper = [w-.15, d-.15, h-.1, w, d, h, 100]
        initial = np.clip(initial, lower, upper)
        def residual(params):
            try:
                return (project(points, params[:3], params[3:6], params[6], size[0]/size[1]) - targets).ravel()
            except (ValueError, FloatingPointError):
                return np.full(len(points)*2, 10.)
        fit = least_squares(residual, initial, bounds=(lower, upper), max_nfev=180, loss="soft_l1", f_scale=.02)
        camera.position_m = tuple(fit.x[:3])
        camera.target_m = tuple(fit.x[3:6])
        camera.lens_mm = float(fit.x[6])
        difference = project(points, fit.x[:3], fit.x[3:6], fit.x[6], size[0]/size[1]) - targets
        errors = np.linalg.norm(difference * [size[0], size[1]], axis=1)/math.hypot(*size)
        scene.projection_report = {"landmark_count": len(points), "median_diagonal": float(np.median(errors)),
                                   "max_diagonal": float(np.max(errors)), "provisional_threshold": [.02, .05]}
        if np.median(errors) > .02 or np.max(errors) > .05:
            scene.readiness = "reference_only"
            scene.unresolved.append("主要対応点の投影誤差が暫定基準を超過。3Dを正式空間として採用できません。")
    else:
        scene.readiness = "reference_only"
        scene.unresolved.append(proposal.unsupported_reason or "投影調整に必要な6点以上の対応点がありません。")
    if not proposal.supported:
        scene.readiness = "reference_only"
    if scene.readiness == "reference_only":
        scene.simplification = "REFERENCE_ONLY"
    elif scene.simplification == "PROPOSE_SIMPLIFIED_MASTER" and manifest.answers.get("policy") != "organize":
        scene.simplification = "SIMPLIFY_INTERNAL"
    scene.cameras[0] = camera
    # Candidate viewpoints are constrained to the same geometry, never moved furniture.
    def free(pos):
        for obj in objects:
            if obj.category == Category.ARCHITECTURE or placements[obj.object_id].presence != "placed":
                continue
            center, rot = world_pose(scene, state, obj.object_id)
            local = rot.inv().apply(np.array(pos)-center)
            ow, od, oh = obj.dimensions_m
            if abs(local[0]) < ow/2+.12 and abs(local[1]) < od/2+.12 and -.1 < local[2] < oh+.1:
                return False
        return True
    for index, (key, label, offset) in enumerate([("left", "左から", (-w*.2, d*.1, 0)),
                                                 ("right", "右から", (w*.2, d*.1, 0)),
                                                 ("high", "高い位置", (0, d*.2, .6))], start=1):
        selected = None
        for factor in np.linspace(1, .15, 20):
            pos = np.clip(np.array(camera.position_m)+np.array(offset)*factor, [.18, .18, .25], [w-.18, d-.18, h-.1])
            if free(pos):
                selected = pos
                break
        if selected is None:
            scene.readiness = "reference_only"
            scene.simplification = "REFERENCE_ONLY"
            scene.unresolved.append(f"{label}: 家具内に入らないカメラ位置が見つかりません")
            selected = camera.position_m
        scene.cameras[index] = camera.model_copy(update={"camera_id": key, "label": label,
                                                          "position_m": tuple(selected), "assumed_faces": ["元画像で見えない面を含む"]})
    if not free(camera.position_m):
        scene.readiness = "reference_only"
        scene.simplification = "REFERENCE_ONLY"
        scene.unresolved.append("基準カメラが家具内に入っています")
    from .validation import layout_warnings
    scene.unresolved.extend(layout_warnings(scene, state))
    states = [state]
    if "class" in proposal.scene_type.lower() or "教室" in proposal.scene_type:
        baseline = state.model_copy(deep=True)
        baseline.state_id = uid("state")
        baseline.parent_state_id = sid
        baseline.story_time = "通常授業の推定基本配置"
        desks = [o for o in objects if o.template_id == "desk" and not any(t in o.label.lower() for t in ('教卓','teacher','講師'))]
        columns = max(1, round(math.sqrt(len(desks))))
        rows = max(1, math.ceil(len(desks)/columns))
        for i, obj in enumerate(desks):
            baseline.objects[obj.object_id].pose = Pose(translation_m=((i%columns+1)*w/(columns+1),
                                                                          (i//columns+1)*d/(rows+1), 0))
        # Chairs keep their pairing with the nearest observed desk; original state is untouched.
        chairs=[o for o in objects if o.template_id=='chair']
        if chairs and desks:
            distances=[[np.linalg.norm(world_pose(scene,state,c.object_id)[0][:2]-world_pose(scene,state,d.object_id)[0][:2]) for d in desks] for c in chairs]
            pairs=zip(*linear_sum_assignment(distances))
        else:pairs=[]
        for chair_index,desk_index in pairs:
            chair,desk=chairs[chair_index],desks[desk_index]
            desk_pos = baseline.objects[desk.object_id].pose.translation_m
            # Board/front is provisionally -Y; the chair back is +Y in this template.
            baseline.objects[chair.object_id].pose = Pose(translation_m=(desk_pos[0], desk_pos[1]+.65, 0))
        baseline.changes = [{"reason": "通常授業の机グリッドは推定。元画像の配置とは別状態。"}]
        scene.baseline_state_id = baseline.state_id
        scene.assumptions.append(Assumption(assumption_id=uid("assumption"), target_id=baseline.state_id,
                                            field_path="objects", proposed_value="通常授業の机グリッド",
                                            reason="指定がない場合の基本配置", impact="structural"))
        validate_state(scene, baseline)
        scene.unresolved.extend(layout_warnings(scene, baseline))
        states.append(baseline)
    from .domain import ManagementPolicy
    scene.management_policy = ManagementPolicy.model_validate(manifest.answers.get("management_policy", {}))
    from .detail_groups import group_ambient_books
    group_ambient_books(scene,state)
    organize_scene(scene)
    from .material_catalog import classify_materials
    classify_materials(scene)
    scene = SceneRevision.model_validate(scene.model_dump(mode="json"))
    return scene, states, assets

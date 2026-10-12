from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(value) -> str:
    def canonical(item):
        if isinstance(item, BaseModel):
            item = item.model_dump(mode="json")
        if isinstance(item, dict):
            return {k: canonical(v) for k, v in item.items() if k not in {"created_at", "updated_at"}}
        if isinstance(item, (list, tuple)):
            return [canonical(v) for v in item]
        return item
    value = canonical(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class DomainError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Category(StrEnum):
    ARCHITECTURE = "architecture"
    FURNITURE = "furniture"
    DECORATION = "decoration"
    PROP = "prop"


class MaterialClass(StrEnum):
    BUILDING = "building"
    STANDARD_FURNITURE = "standard_furniture"
    FABRIC_FURNITURE = "fabric_furniture"
    STANDARD_PROP = "standard_prop"
    FABRIC_PROP = "fabric_prop"


class ObjectComponent(Model):
    component_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str
    material_class: MaterialClass
    reference_ids: list[str] = Field(default_factory=list)
    rendering: Literal["rigid", "soft_fabric", "overlay_optional"] = "rigid"


class Evidence(Model):
    basis: Literal["observed", "estimated", "user_specified", "unknown"]
    reason: str
    review_status: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"


class Pose(Model):
    translation_m: tuple[float, float, float]
    quaternion_wxyz: tuple[float, float, float, float] = (1, 0, 0, 0)
    anchor_ref: str = "world"
    anchor_key: Literal["origin", "top"] = "origin"

    @model_validator(mode="after")
    def normalized(self):
        if abs(sum(x*x for x in self.quaternion_wxyz) - 1) > .001:
            raise ValueError("quaternion must be normalized")
        return self


class Opening(Model):
    host_wall: Literal["back", "left", "right", "front"]
    rows: int = Field(ge=1, le=8)
    columns_per_row: list[int]
    opening_method: Literal["hinged", "sliding", "fixed"] | None = None
    sill_type: Literal["waist", "floor", "unknown"] = "unknown"
    row_height_ratios: list[float] | None = None
    column_width_ratios: list[list[float]] | None = None

    @model_validator(mode="after")
    def panes(self):
        if len(self.columns_per_row) != self.rows or any(c < 1 or c > 12 for c in self.columns_per_row):
            raise ValueError("pane subdivision does not match rows")
        if self.row_height_ratios is not None:
            if len(self.row_height_ratios) != self.rows or min(self.row_height_ratios) <= 0 or abs(sum(self.row_height_ratios)-1) > .001:
                raise ValueError("row ratios must be positive and sum to 1")
        if self.column_width_ratios is not None:
            if len(self.column_width_ratios) != self.rows:
                raise ValueError("column ratios require all rows")
            for ratios, count in zip(self.column_width_ratios, self.columns_per_row):
                if len(ratios) != count or min(ratios) <= 0 or abs(sum(ratios)-1) > .001:
                    raise ValueError("column ratios must be positive and sum to 1")
        return self


Template = Literal["floor", "ceiling", "wall", "window", "door", "desk", "chair", "bed", "shelf", "box", "decoration", "rug", "plant", "phone", "pen", "book"]
ManagementRole = Literal["auto", "building", "fixed_furniture", "movable_prop", "ambient_decoration"]
SURFACE_PARTS = {"desk": {"desk_top"}, "bed": {"bed_mattress"}, "shelf": {"shelf_back"},
                 "chair": {"seat"}, "plant": {"plant_pot"},
                 "rug": {"body"}, "phone": {"body", "phone_screen"}, "pen": {"body"}, "book": {"body"},
                 "box": {"body"}, "decoration": {"body"}, "floor": {"body"}, "ceiling": {"body"}}


class ManagementPolicy(Model):
    max_geometry_units: int = Field(default=50000, ge=100, le=1000000)
    max_detailed_movable: int = Field(default=12, ge=0, le=512)


class SurfaceMapping(Model):
    """Object-local detail mapping, never a whole-scene background image."""
    target_part: Literal["body", "desk_top", "bed_mattress", "shelf_back", "seat", "plant_pot", "phone_screen"] = "body"
    face: Literal["top", "front", "back", "left", "right"]
    usage: Literal["color", "bump"]
    rotation_degrees: Literal[0, 90, 180, 270] = 0
    repeat_uv: tuple[float, float] = (1, 1)
    bump_distance_m: float = Field(default=.001, gt=0, le=.005)

    @model_validator(mode="after")
    def bounded_repeat(self):
        if any(v <= 0 or v > 32 for v in self.repeat_uv):
            raise ValueError("surface repeat must be >0 and <=32")
        return self


class SceneObject(Model):
    object_id: str
    label: str
    category: Category
    importance: Literal["structural", "narrative", "ambient", "unknown"]
    representation: Literal["template", "proxy", "group", "reference_only"] = "template"
    template_id: Template
    dimensions_m: tuple[float, float, float]
    opening: Opening | None = None
    field_locks: list[str] = Field(default_factory=list)
    change_policy: Literal["locked", "explicit_only", "scene_change_allowed"] = "explicit_only"
    reference_ids: list[str] = Field(default_factory=list)
    variants: dict[str, str] = Field(default_factory=lambda: {"default": "original"})
    attribute_evidence: dict[str, Evidence] = Field(default_factory=dict)
    derived_from_group: str | None = None
    management_role: ManagementRole = "auto"
    geometry_detail: Literal["standard", "proxy"] = "standard"
    material_class: MaterialClass | None = None
    components: list[ObjectComponent] = Field(default_factory=list)

    @model_validator(mode="after")
    def dimensions(self):
        if any(d <= 0 or d > 100 for d in self.dimensions_m):
            raise ValueError("dimensions must be positive and <=100m")
        if self.template_id in ("window", "door") and self.opening is None:
            raise ValueError("opening required for window/door")
        if len({c.component_id for c in self.components}) != len(self.components):
            raise ValueError("duplicate component IDs within an object")
        if any(r not in self.reference_ids for c in self.components for r in c.reference_ids):
            raise ValueError("component references must belong to their object")
        return self


class ObjectState(Model):
    presence: Literal["placed", "stored", "absent", "unknown"]
    pose: Pose | None = None
    container_object_id: str | None = None
    open_amount: float = Field(default=0, ge=0, le=1)
    variant_id: str = "default"
    change_reason: str = "initial interpretation"

    @model_validator(mode="after")
    def presence_contract(self):
        if (self.presence == "placed") != (self.pose is not None):
            raise ValueError("only placed objects have a pose")
        if (self.presence == "stored") != (self.container_object_id is not None):
            raise ValueError("only stored objects have a container")
        return self


class SceneState(Model):
    schema_version: Literal[1] = 1
    state_id: str
    revision_id: str
    parent_state_id: str | None = None
    story_scene_id: str
    story_time: str
    objects: dict[str, ObjectState]
    changes: list[dict] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class CameraPreset(Model):
    camera_id: str
    label: str
    position_m: tuple[float, float, float]
    target_m: tuple[float, float, float]
    projection: Literal["perspective", "orthographic"] = "perspective"
    lens_mm: float = Field(default=24, ge=8, le=200)
    ortho_scale_m: float = Field(default=6, gt=0)
    sensor_width_mm: float = 36
    shift_x: float = 0
    shift_y: float = 0
    resolution: tuple[int, int] = (640, 480)
    clip: tuple[float, float] = (.02, 200)
    assumed_faces: list[str] = Field(default_factory=list)


class ImageAsset(Model):
    asset_id: str
    relative_path: str
    sha256: str
    width: int
    height: int
    format: str
    role: Literal["source", "normalized", "crop", "simplified", "exterior"]
    source_asset_id: str | None = None


class MasterImage(Model):
    asset_id: str
    source_asset_id: str
    mode: Literal["original", "simplified"] = "original"
    change_summary: list[str] = Field(default_factory=list)


class ReferenceEntry(Model):
    reference_id: str
    owner_object_id: str
    source_asset_id: str
    normalized_asset_id: str
    bbox_xywh: tuple[float, float, float, float]
    crop_asset_id: str
    visible_surface: str
    role: Literal["shape", "pattern", "text", "material", "layout"]
    description: str
    text_content: str | None = None
    text_is_verified: bool = False
    applicable_variants: list[str] = Field(default_factory=lambda: ["default"])
    occlusion_notes: str = "May include occluding objects; not a UV texture."
    surface_mapping: SurfaceMapping | None = None
    surface_patch_verified: bool = False

    @model_validator(mode="after")
    def safe_mapping(self):
        if self.surface_mapping and (not self.surface_patch_verified or self.role not in {"pattern", "material"}):
            raise ValueError("mapped detail must be a verified surface-only pattern/material patch")
        return self


class Assumption(Model):
    assumption_id: str
    target_id: str
    field_path: str
    proposed_value: str
    reason: str
    impact: Literal["structural", "appearance"]
    review_status: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"


class ContinuityRule(Model):
    version: Literal[1] = 1
    default_inherit: bool = True
    day_change_causes_movement: Literal[False] = False


class SavedGeometry(Model):
    """An immutable project-relative scene to render through the existing Blender MCP addon."""
    relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backend: Literal["blender_mcp"] = "blender_mcp"

    @model_validator(mode="after")
    def relative_only(self):
        from pathlib import PurePosixPath
        path=PurePosixPath(self.relative_path.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or any(":" in p for p in path.parts) or path.suffix!=".blend":
            raise ValueError("saved geometry requires a safe project-relative .blend path")
        return self


class DetailGroup(Model):
    group_id: str
    label: str
    support_object_id: str
    member_object_ids: list[str] = Field(min_length=1)
    estimated_count: int = Field(ge=1,le=1000)
    count_evidence: Evidence
    rendering: Literal['surface_proxy'] = 'surface_proxy'
    reference_ids: list[str] = Field(default_factory=list)


class RoomSystems(Model):
    """Persisted prepared-scene checks; drawing style never bypasses these."""
    flush_ceiling_light_id: str | None = None
    air_conditioner_id: str | None = None
    curtain_id: str | None = None
    minimum_ac_curtain_gap_m: float = Field(default=.15,ge=.01,le=1)
    ac_front_service_m: float = Field(default=.30,ge=.05,le=2)
    exterior_mode: Literal['none','source_image_only']='none'
    exterior_source_sha256: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    exterior_crop_rectangles: list[tuple[int,int,int,int]] = Field(default_factory=list)
    exterior_packed_image: str | None = None
    exterior_asset_id: str | None = None
    exterior_angular_width: float = Field(default=1.15,ge=.1,le=10)
    exterior_angular_height: float = Field(default=.80,ge=.1,le=10)
    exterior_vertical_center: float = Field(default=.50,ge=-2,le=2)


class SceneRevision(Model):
    schema_version: Literal[1, 2, 3] = 3
    revision_id: str
    scene_id: str
    source_asset_id: str
    master_image: MasterImage
    scene_type: str
    usage_purpose: str
    room_dimensions_m: tuple[float, float, float]
    coordinate_definition: str = "Right handed metres; Z up; origin front-left floor corner; X right, Y depth."
    objects: list[SceneObject]
    references: list[ReferenceEntry]
    cameras: list[CameraPreset]
    source_state_id: str
    baseline_state_id: str
    continuity_rules: ContinuityRule = Field(default_factory=ContinuityRule)
    assumptions: list[Assumption] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    readiness: Literal["review_ready", "reference_only"] = "review_ready"
    simplification: Literal["KEEP_SOURCE", "SIMPLIFY_INTERNAL", "PROPOSE_SIMPLIFIED_MASTER", "REFERENCE_ONLY"] = "KEEP_SOURCE"
    projection_report: dict = Field(default_factory=dict)
    management_policy: ManagementPolicy = Field(default_factory=ManagementPolicy)
    management_report: dict = Field(default_factory=dict)
    saved_geometry: SavedGeometry | None = None
    detail_groups: list[DetailGroup] = Field(default_factory=list)
    room_systems: RoomSystems | None = None

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [o.object_id for o in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate object IDs")
        if self.room_systems:
            for oid in (self.room_systems.flush_ceiling_light_id,self.room_systems.air_conditioner_id,self.room_systems.curtain_id):
                if oid is not None and oid not in ids:raise ValueError('unknown room fixture ID')
            if self.room_systems.exterior_mode=='source_image_only' and not (self.room_systems.exterior_source_sha256 and self.room_systems.exterior_packed_image):
                raise ValueError('source-only exterior needs its image/hash provenance')
        grouped=[]
        if len({g.group_id for g in self.detail_groups})!=len(self.detail_groups):raise ValueError('duplicate detail groups')
        for group in self.detail_groups:
            if group.support_object_id not in ids or set(group.member_object_ids)-set(ids):raise ValueError('unknown grouped objects/support')
            grouped.extend(group.member_object_ids)
        if len(grouped)!=len(set(grouped)):raise ValueError('object belongs to multiple detail groups')
        if len({c.camera_id for c in self.cameras}) != len(self.cameras):
            raise ValueError("duplicate camera IDs")
        if self.readiness == "review_ready" and len(self.cameras) != 4:
            raise ValueError("four cameras required")
        references = {r.reference_id: r for r in self.references}
        if len(references) != len(self.references):
            raise ValueError("duplicate reference IDs")
        catalog = {o.object_id: o for o in self.objects}
        mapped = set()
        for ref in self.references:
            if ref.owner_object_id not in catalog:
                raise ValueError("reference owner is missing")
            if ref.reference_id not in catalog[ref.owner_object_id].reference_ids:
                raise ValueError("reference is not linked by its owner")
            if ref.surface_mapping:
                owner = catalog[ref.owner_object_id]
                if ref.surface_mapping.target_part not in SURFACE_PARTS.get(owner.template_id, set()):
                    raise ValueError("surface target part is unsupported for this object")
                if owner.geometry_detail == "proxy" and ref.surface_mapping.target_part != "body":
                    raise ValueError("proxy must retain the part used by its surface mapping")
                key = (ref.owner_object_id, ref.surface_mapping.target_part, ref.surface_mapping.face,
                       ref.surface_mapping.usage)
                for variant in ref.applicable_variants:
                    if (*key, variant) in mapped:
                        raise ValueError("duplicate surface mapping for the same variant")
                    mapped.add((*key, variant))
        for obj in self.objects:
            if any(rid not in references or references[rid].owner_object_id != obj.object_id for rid in obj.reference_ids):
                raise ValueError("object reference link is missing or belongs to another owner")
        return self


class ProjectManifest(Model):
    schema_version: Literal[1] = 1
    project_id: str
    title: str
    created_at: str = Field(default_factory=now)
    active_revision_id: str | None = None
    active_state_id: str | None = None
    assets: list[ImageAsset]
    answers: dict
    adoption_records: list[dict] = Field(default_factory=list)


def validate_state(scene: SceneRevision, state: SceneState):
    catalog = {o.object_id: o for o in scene.objects}
    if state.revision_id != scene.revision_id or set(state.objects) != set(catalog):
        raise DomainError("STATE_INVALID", "state must contain all objects from the same revision")
    for oid, item in state.objects.items():
        obj = catalog[oid]
        if item.variant_id not in obj.variants:
            raise DomainError("STATE_INVALID", "unregistered variant")
        if item.open_amount and obj.template_id not in ("window", "door"):
            raise DomainError("STATE_INVALID", "object cannot open")
        if item.open_amount and (obj.opening.opening_method in (None, "fixed") or
                                 obj.template_id == "window" and obj.opening.opening_method == "hinged"):
            raise DomainError("STATE_INVALID", "開閉方式が未確認、固定、または未対応です。新改訂で方式を確認してください。")
        visited = {oid}
        current = item
        while True:
            anchor = (current.pose.anchor_ref if current.pose else current.container_object_id) or "world"
            if anchor == "world":
                break
            if anchor not in catalog or anchor in visited:
                raise DomainError("STATE_INVALID", "anchor/container missing or cyclic")
            visited.add(anchor)
            current = state.objects[anchor]
            if current.presence != "placed":
                raise DomainError("STATE_INVALID", "support/container must be placed")


def new_state(scene: SceneRevision, parent: SceneState, story_time: str, commands: list[dict],
              story_scene_id: str | None = None) -> SceneState:
    validate_state(scene, parent)
    data = parent.model_dump(mode="json")
    data.update(state_id=uid("state"), parent_state_id=parent.state_id,
                story_time=story_time, story_scene_id=story_scene_id or parent.story_scene_id,
                created_at=now(), changes=copy.deepcopy(commands))
    catalog = {o.object_id: o for o in scene.objects}
    for command in commands:
        if set(command) - {"object_id", "op", "value", "reason"}:
            raise DomainError("COMMAND_INVALID", "unsupported command field")
        oid = command.get("object_id")
        if oid not in catalog:
            raise DomainError("COMMAND_INVALID", "unknown object")
        if any(oid in g.member_object_ids for g in scene.detail_groups):
            raise DomainError('COMMAND_INVALID','まとまりの本は新しい改訂で独立した重要物にしてから変更してください。')
        obj = catalog[oid]
        op = command.get("op")
        field = {"Move": "placement", "SetPresence": "placement", "SetOpenAmount": "open_amount", "SetVariant": "content"}.get(op)
        if field is None:
            raise DomainError("COMMAND_INVALID", "use a new revision for structural changes")
        if obj.change_policy == "locked" or field in obj.field_locks:
            raise DomainError("OBJECT_LOCKED", f"{obj.label}: {field}")
        value = command.get("value")
        item = data["objects"][oid]
        if op == "Move":
            item.update(presence="placed", pose=Pose.model_validate(value).model_dump(), container_object_id=None)
        elif op == "SetPresence":
            if value.get("presence") == "placed":
                raise DomainError("COMMAND_INVALID", "use Move to place an object")
            item.update(presence=value["presence"], pose=None, container_object_id=value.get("container_object_id"))
        elif op == "SetOpenAmount":
            item["open_amount"] = value
        else:
            item["variant_id"] = value
        item["change_reason"] = command.get("reason") or "explicit user command"
    result = SceneState.model_validate(data)
    validate_state(scene, result)
    return result


def world_pose(scene: SceneRevision, state: SceneState, oid: str):
    """Resolve anchors with quaternion multiplication. Same math is used by worker."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    obj = next(o for o in scene.objects if o.object_id == oid)
    pose = state.objects[oid].pose
    if pose is None:
        raise DomainError("STATE_INVALID", "object is not placed")
    q = pose.quaternion_wxyz
    rot = Rotation.from_quat([q[1], q[2], q[3], q[0]])
    pos = np.array(pose.translation_m)
    if pose.anchor_ref != "world":
        parent_pos, parent_rot = world_pose(scene, state, pose.anchor_ref)
        parent = next(o for o in scene.objects if o.object_id == pose.anchor_ref)
        offset = np.array([0, 0, parent.dimensions_m[2] if pose.anchor_key == "top" else 0])
        pos = parent_pos + parent_rot.apply(offset + pos)
        rot = parent_rot * rot
    return pos, rot

"""Resolve ID/face/variant-linked images into portable Blender inputs."""
from pathlib import Path

from PIL import Image, ImageOps

from .domain import DomainError, SURFACE_PARTS
from .storage import file_hash, read_json, safe_path

def validate_surface_targets(scene):
    catalog = {o.object_id: o for o in scene.objects}
    for ref in scene.references:
        if ref.surface_mapping:
            owner = catalog[ref.owner_object_id]
            if ref.surface_mapping.target_part not in SURFACE_PARTS.get(owner.template_id, set()):
                raise DomainError("SURFACE_INVALID", f"{owner.label}: 表面画像の対象部品が対応していません。")


def prepare_surfaces(scene, state, job_dir: Path):
    validate_surface_targets(scene)
    mapped = [r for r in scene.references if r.surface_mapping and
              state.objects[r.owner_object_id].presence == "placed" and
              state.objects[r.owner_object_id].variant_id in r.applicable_variants]
    if not mapped:
        return []
    root = job_dir.parent.parent
    manifest_path = safe_path(root, "project.json")
    if not manifest_path.is_file():
        raise DomainError("SURFACE_INVALID", "画像の正本プロジェクトがありません。")
    assets = {a["asset_id"]: a for a in read_json(manifest_path)["assets"]}
    if (job_dir / "assets.json").is_file():
        assets.update({a["asset_id"]: a for a in read_json(job_dir / "assets.json")})
    result = []
    textures = job_dir / "output" / "textures"
    textures.mkdir(parents=True, exist_ok=True)
    for index, ref in enumerate(mapped):
        asset = assets.get(ref.crop_asset_id)
        if asset is None or asset["role"] != "crop":
            raise DomainError("SURFACE_INVALID", "表面部分の切り出し画像が未登録です。")
        source = safe_path(root, asset["relative_path"])
        if file_hash(source) != asset["sha256"]:
            raise DomainError("OUTPUT_INVALID", "表面参照画像のハッシュが変化しています。")
        target = textures / f"surface_{index:03d}.png"
        with Image.open(source) as im:
            if min(im.size) < 4:
                raise DomainError("SURFACE_INVALID", "表面参照画像が小さすぎます。")
            ImageOps.grayscale(im).save(target)
        result.append({"reference_id": ref.reference_id, "object_id": ref.owner_object_id,
                       "asset_id": ref.crop_asset_id, "source_sha256": asset["sha256"],
                       "path": target.relative_to(job_dir / "output").as_posix(), "sha256": file_hash(target),
                       **ref.surface_mapping.model_dump(mode="json")})
    return result

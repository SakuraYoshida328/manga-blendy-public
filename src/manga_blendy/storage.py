from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from filelock import FileLock
from PIL import Image, ImageOps

from .domain import (DomainError, ImageAsset, ProjectManifest, SceneRevision,
                     SceneState, content_hash, now, uid, validate_state)

MAX_UPLOAD = 20 * 1024 * 1024


def safe_path(root: Path, relative: str) -> Path:
    part = PurePosixPath(relative.replace("\\", "/"))
    if not part.parts or part.is_absolute() or any(p in ("..", "") or ":" in p for p in part.parts):
        raise DomainError("PATH_INVALID", "relative project path required")
    target = root.joinpath(*part.parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise DomainError("PATH_INVALID", "path escaped project")
    return target


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, data):
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as stream:
        temp = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        for attempt in range(5):
            try:
                os.replace(temp,path)
                break
            except PermissionError as exc:
                if getattr(exc,'winerror',None) not in {5,32,33} or attempt==4:raise
                time.sleep(.1*(attempt+1))
    finally:
        temp.unlink(missing_ok=True)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image_asset(root: Path, relative: str, role: str, source_id=None) -> ImageAsset:
    path = safe_path(root, relative)
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        width, height, fmt = im.width, im.height, im.format
    return ImageAsset(asset_id=uid("asset"), relative_path=relative, sha256=file_hash(path),
                      width=width, height=height, format=fmt, role=role, source_asset_id=source_id)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def project(self, project_id: str) -> Path:
        if not project_id.startswith("project_") or not project_id[8:].isalnum():
            raise DomainError("PROJECT_INVALID", "invalid project ID")
        path = safe_path(self.root, project_id)
        if not (path / "project.json").exists():
            raise DomainError("PROJECT_INVALID", "project not found")
        return path

    def manifest(self, project_id: str) -> ProjectManifest:
        return ProjectManifest.model_validate(read_json(self.project(project_id) / "project.json"))

    def list_projects(self):
        results = []
        for path in sorted(self.root.glob("project_*/project.json")):
            try:
                results.append(ProjectManifest.model_validate(read_json(path)))
            except (ValueError, OSError):
                continue
        return results

    def create(self, title: str, data: bytes, answers: dict) -> ProjectManifest:
        if len(data) > MAX_UPLOAD:
            raise DomainError("INPUT_INVALID", "image exceeds 20MB")
        try:
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in ("PNG", "JPEG", "WEBP") or im.width * im.height > 40_000_000:
                    raise ValueError("unsupported image size/format")
                fmt = im.format.lower().replace("jpeg", "jpg")
                normalized = ImageOps.exif_transpose(im).convert("RGB")
        except Exception as exc:
            raise DomainError("INPUT_INVALID", "PNG/JPEG/WebP image required") from exc
        pid = uid("project")
        root = self.root / pid
        (root / "assets/source").mkdir(parents=True)
        (root / "assets/normalized").mkdir()
        relative = f"assets/source/original.{fmt}"
        (root / relative).write_bytes(data)
        source = image_asset(root, relative, "source")
        normalized.save(root / "assets/normalized/analysis.png")
        normal = image_asset(root, "assets/normalized/analysis.png", "normalized", source.asset_id)
        manifest = ProjectManifest(project_id=pid, title=title[:200], assets=[source, normal], answers=answers)
        atomic_json(root / "project.json", manifest)
        return manifest

    def revision(self, pid: str, rid: str) -> SceneRevision:
        return SceneRevision.model_validate(read_json(safe_path(self.project(pid), f"revisions/{rid}/scene.json")))

    def state(self, pid: str, sid: str) -> SceneState:
        return SceneState.model_validate(read_json(safe_path(self.project(pid), f"states/{sid}.json")))

    def save_state(self, pid: str, state: SceneState, expected_state: str | None):
        root = self.project(pid)
        with FileLock(str(root / ".project.lock")):
            manifest = self.manifest(pid)
            if manifest.active_state_id != expected_state:
                raise DomainError("REVISION_CONFLICT", "project changed; reload before applying")
            scene = self.revision(pid, state.revision_id)
            validate_state(scene, state)
            destination = safe_path(root, f"states/{state.state_id}.json")
            if destination.exists():
                raise DomainError("REVISION_CONFLICT", "state is immutable")
            atomic_json(destination, state)
            manifest.active_state_id = state.state_id
            manifest.active_revision_id = state.revision_id
            atomic_json(root / "project.json", manifest)

    def adopt(self, pid: str, job_id: str, expected_revision: str | None, simplified=False):
        root = self.project(pid)
        job = safe_path(root, f"jobs/{job_id}")
        with FileLock(str(root / ".project.lock")):
            manifest = self.manifest(pid)
            record = next((r for r in manifest.adoption_records if r["job_id"] == job_id), None)
            if record:
                return manifest
            job_data = read_json(job / "job.json")
            if job_data["status"] != "succeeded":
                raise DomainError("OUTPUT_INVALID", "only validated successful candidates can be adopted")
            if manifest.active_revision_id != expected_revision:
                raise DomainError("REVISION_CONFLICT", "active revision changed")
            scene = SceneRevision.model_validate(read_json(job / "candidate_scene.json"))
            if scene.saved_geometry is not None:
                geometry = safe_path(root, scene.saved_geometry.relative_path)
                if not geometry.is_file() or file_hash(geometry) != scene.saved_geometry.sha256:
                    raise DomainError("OUTPUT_INVALID", "漫画仕上げ用3Dのハッシュが一致しません。")
                scene.saved_geometry.relative_path = f"revisions/{scene.revision_id}/lightweight.blend"
            states = [SceneState.model_validate(v) for v in read_json(job / "candidate_states.json")]
            adopt_state_id=job_data.get('adopt_state_id',scene.source_state_id)
            if adopt_state_id not in {s.state_id for s in states}:raise DomainError('STATE_INVALID','採用する状態が候補にありません。')
            for state in states:
                validate_state(scene, state)
            if simplified:
                if scene.simplification != "PROPOSE_SIMPLIFIED_MASTER":
                    raise DomainError("INPUT_INVALID", "no simplified master candidate")
                asset = image_asset(root, f"jobs/{job_id}/output/source.png", "simplified", scene.source_asset_id)
                target = f"assets/simplified/{asset.asset_id}.png"
                safe_path(root, target).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(safe_path(root, asset.relative_path), safe_path(root, target))
                asset.relative_path = target
                manifest.assets.append(asset)
                scene.master_image.asset_id = asset.asset_id
                scene.master_image.mode = "simplified"
                scene.master_image.change_summary = ["構造図をマスター候補として明示採用。細部は元画像の参照カードに保持。"]
            destination = safe_path(root, f"revisions/{scene.revision_id}")
            if destination.exists():
                # An interrupted adoption may have completed files before switching manifest.
                if content_hash(read_json(destination / "scene.json")) != content_hash(scene):
                    raise DomainError("REVISION_CONFLICT", "revision exists with different content")
            else:
                staging = root / "jobs" / job_id / "adoption"
                staging.mkdir(exist_ok=True)
                atomic_json(staging / "scene.json", scene)
                shutil.copyfile(job / "validation.json", staging / "validation.json")
                if scene.saved_geometry is not None:
                    shutil.copyfile(geometry, staging / "lightweight.blend")
                elif (job / "output/lightweight.blend").exists():
                    shutil.copyfile(job / "output/lightweight.blend", staging / "lightweight.blend")
                (staging / "interpretation.md").write_text(interpretation(scene), encoding="utf-8")
                destination.parent.mkdir(exist_ok=True)
                os.replace(staging, destination)
            for state in states:
                path = safe_path(root, f"states/{state.state_id}.json")
                if path.exists() and content_hash(read_json(path)) != content_hash(state):
                    raise DomainError("REVISION_CONFLICT", "immutable state conflict")
                if not path.exists():
                    atomic_json(path, state)
            for asset_data in read_json(job / "assets.json"):
                asset = ImageAsset.model_validate(asset_data)
                if asset.asset_id not in {a.asset_id for a in manifest.assets}:
                    manifest.assets.append(asset)
            if (job / "output/result.json").exists():
                result = read_json(job / "output/result.json")
                result_state_id=result.get('state_id',scene.source_state_id)
                if result_state_id not in {s.state_id for s in states}:raise DomainError('STATE_INVALID','出力の状態が候補と異なります。')
                render_dir = safe_path(root, f"renders/{scene.revision_id}/{result_state_id}/{result['input_hash']}")
                if not render_dir.exists():
                    shutil.copytree(job / "output", render_dir)
            manifest.active_revision_id = scene.revision_id
            manifest.active_state_id = adopt_state_id
            manifest.adoption_records.append({"job_id": job_id, "revision_id": scene.revision_id, "at": now()})
            atomic_json(root / "project.json", manifest)
            return manifest

    def export(self, pid: str) -> Path:
        root = self.project(pid)
        eid = uid("export")
        destination = root / "exports" / eid
        destination.mkdir(parents=True)
        with FileLock(str(root / ".project.lock")):
            manifest = self.manifest(pid)
            assets = {a.asset_id: a for a in manifest.assets}
            entries = ["# " + manifest.title, "", "正本: version付きJSON / 画像: 構造確認用", ""]
            for scene_path in root.glob("revisions/*/scene.json"):
                scene = SceneRevision.model_validate(read_json(scene_path))
                entries.append(interpretation(scene))
                for ref in scene.references:
                    entries += [f"### {ref.owner_object_id} / {ref.visible_surface}", ref.description,
                                "適用variant: " + ", ".join(ref.applicable_variants) + "。他のvariantには使用しない。",
                                f"![参照]({assets[ref.crop_asset_id].relative_path})", ""]
            index = "\n".join(entries)
            (destination / "index.md").write_text(index, encoding="utf-8")
            archive = destination / "package.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("index.md", index)
                files = [root / "project.json"]
                for directory in ("assets", "revisions", "states", "renders",'improvements'):
                    files.extend(p for p in (root / directory).rglob("*") if p.is_file() and p.suffix.lower() in {".json", ".png", ".jpg", ".webp", ".blend", ".md"})
                for path in files:
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        raise DomainError("PATH_INVALID", "foreign/symlink file in export")
                    z.write(path, path.relative_to(root).as_posix())
            return archive

    def import_zip(self, data: bytes) -> str:
        if len(data) > 500 * 1024 * 1024:
            raise DomainError("INPUT_INVALID", "archive too large")
        staging = self.root / uid("import")
        staging.mkdir()
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if sum(i.file_size for i in z.infolist()) > 1_000_000_000:
                    raise DomainError("INPUT_INVALID", "expanded archive too large")
                for info in z.infolist():
                    path = safe_path(staging, info.filename)
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise DomainError("PATH_INVALID", "symlink in archive")
                    top = PurePosixPath(info.filename).parts[0]
                    if top not in {"assets", "revisions", "states", "renders",'improvements', "project.json", "index.md"}:
                        raise DomainError("PATH_INVALID", "unexpected archive file")
                    if not info.is_dir():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(z.read(info))
            manifest = ProjectManifest.model_validate(read_json(staging / "project.json"))
            for asset in manifest.assets:
                if file_hash(safe_path(staging, asset.relative_path)) != asset.sha256:
                    raise DomainError("OUTPUT_INVALID", "asset checksum mismatch")
            for path in staging.glob("revisions/*/scene.json"):
                scene = SceneRevision.model_validate(read_json(path))
                if scene.saved_geometry is not None and file_hash(safe_path(staging, scene.saved_geometry.relative_path)) != scene.saved_geometry.sha256:
                    raise DomainError("OUTPUT_INVALID", "保存3Dのハッシュが一致しません。")
                if scene.source_asset_id not in {a.asset_id for a in manifest.assets}:
                    raise DomainError("OUTPUT_INVALID", "missing source asset")
                for sid in {scene.source_state_id, scene.baseline_state_id}:
                    state = SceneState.model_validate(read_json(safe_path(staging, f"states/{sid}.json")))
                    validate_state(scene, state)
                if any(ref.crop_asset_id not in {a.asset_id for a in manifest.assets} for ref in scene.references):
                    raise DomainError("OUTPUT_INVALID", "missing reference asset")
            for path in staging.glob("states/*.json"):
                state = SceneState.model_validate(read_json(path))
                scene = SceneRevision.model_validate(read_json(safe_path(staging, f"revisions/{state.revision_id}/scene.json")))
                validate_state(scene, state)
            if manifest.active_revision_id:
                safe_path(staging, f"revisions/{manifest.active_revision_id}/scene.json").read_bytes()
                safe_path(staging, f"states/{manifest.active_state_id}.json").read_bytes()
            manifest.project_id = uid("project")
            atomic_json(staging / "project.json", manifest)
            os.replace(staging, self.root / manifest.project_id)
            return manifest.project_id
        except DomainError:
            raise
        except (ValueError, OSError, zipfile.BadZipFile, KeyError) as exc:
            raise DomainError("INPUT_INVALID", "資料ZIPが破損しているか、参照先が欠けています。") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def interpretation(scene: SceneRevision) -> str:
    lines = [f"## {scene.revision_id}", f"場所: {scene.scene_type}",
             f"状態: {scene.readiness} / {scene.simplification}",
             "", "### 観測・推定", ""]
    for obj in scene.objects:
        evidence = "; ".join(f"{k}: {v.basis} ({v.reason})" for k, v in obj.attribute_evidence.items())
        lines.append(f"- {obj.label} [{obj.object_id}] {obj.template_id}: {evidence}")
    lines += ["", "### 仮定", ""] + [f"- {a.proposed_value}: {a.reason}" for a in scene.assumptions]
    lines += ["", "### 未解決", ""] + [f"- {u}" for u in scene.unresolved]
    if scene.management_report:
        from .organization import ROLE_LABELS
        lines += ["", "### 分類と簡素化", "運用予算は暫定値で、AI管理能力の実測値ではありません。"]
        for obj in scene.objects:
            lines.append(f"- {obj.label} [{obj.object_id}]: {ROLE_LABELS.get(obj.management_role,obj.management_role)} / {obj.geometry_detail}")
        lines += [f"- {s['object_id']}: {s['reason']}" for s in scene.management_report.get("simplification_steps", [])]
    lines += ["", "### 3Dと詳細画像の対応"]
    for ref in scene.references:
        mapping = ref.surface_mapping
        lines.append(f"- {ref.owner_object_id} ↔ {ref.reference_id} ↔ {ref.crop_asset_id}: " +
                     (f"{mapping.target_part}/{mapping.face}/{mapping.usage}" if mapping else "詳細参照のみ"))
    return "\n".join(lines)

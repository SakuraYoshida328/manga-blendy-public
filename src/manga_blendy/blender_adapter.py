import subprocess
import time
from pathlib import Path

import psutil
from PIL import Image, ImageStat

from .domain import DomainError, content_hash, validate_state
from .storage import atomic_json, file_hash, read_json
from .appearance import prepare_surfaces

ENTRY = Path(__file__).parent / "blender_worker" / "entry.py"


def diagnose(path: str):
    executable = Path(path)
    if not executable.is_file():
        raise DomainError("BLENDER_NOT_FOUND", "Blender実行ファイルが見つかりません。")
    try:
        result = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                                timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DomainError("BLENDER_FAILED", "Blenderのバージョン確認に失敗しました。") from exc
    version = result.stdout.splitlines()[0] if result.stdout else "unknown"
    if result.returncode or not version.startswith("Blender 5.2"):
        raise DomainError("BLENDER_VERSION", f"Blender 5.2が必要です: {version}")
    return version


def terminate_owned(pid, created):
    try:
        process = psutil.Process(pid)
        if abs(process.create_time()-created) > .1:
            return
        children = process.children(recursive=True)
        for child in reversed(children):
            child.kill()
        process.kill()
        psutil.wait_procs(children+[process], timeout=5)
    except psutil.Error:
        pass


def execute(executable, request_file, output, timeout, cancelled, on_process,entry=ENTRY):
    with (output / "blender.log").open("wb") as log:
        process = subprocess.Popen([executable, "--background", "--factory-startup", "--disable-autoexec",
                                    "--python-exit-code", "1", "--python", str(entry), "--", str(request_file)],
                                   stdout=log, stderr=subprocess.STDOUT, shell=False,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        created = psutil.Process(process.pid).create_time()
        on_process(process.pid, created)
        started = time.monotonic()
        while process.poll() is None:
            if cancelled():
                terminate_owned(process.pid, created)
                raise DomainError("CANCELLED", "処理を停止しました。")
            if time.monotonic()-started > timeout:
                terminate_owned(process.pid, created)
                raise DomainError("BLENDER_TIMEOUT", "Blenderの制限時間を超過しました。")
            time.sleep(.25)
        if process.returncode:
            raise DomainError("BLENDER_FAILED", f"Blender終了コード{process.returncode}。ジョブログを確認してください。")


def render(scene, state, job_dir: Path, config, cancelled=lambda: False, on_process=lambda *args: None):
    validate_state(scene, state)
    surfaces = prepare_surfaces(scene, state, job_dir)
    version = diagnose(config["blender_path"])
    input_hash = content_hash({"scene": scene.model_dump(mode="json"), "state": state.model_dump(mode="json"),
                               "generator": file_hash(ENTRY), "blender": version, "samples": config["render_samples"],
                               "surfaces": surfaces})
    output = job_dir / "output"
    output.mkdir(exist_ok=True)
    if (output / "result.json").exists():
        existing = read_json(output / "result.json")
        if existing["input_hash"] == input_hash:
            validate_output(output, scene, state, input_hash)
            return existing
    request = {"scene": scene.model_dump(mode="json"), "state": state.model_dump(mode="json"),
               "output_dir": str(output), "input_hash": input_hash, "samples": config["render_samples"],
               "surfaces": surfaces}
    atomic_json(job_dir / "request.json", request)
    execute(config["blender_path"], job_dir / "request.json", output, config["blender_timeout"], cancelled, on_process)
    atomic_json(job_dir / "verify_request.json", {"mode": "verify", "output_dir": str(output)})
    execute(config["blender_path"], job_dir / "verify_request.json", output, 60, cancelled, on_process)
    validate_output(output, scene, state, input_hash)
    return read_json(output / "result.json")


def validate_output(output: Path, scene, state, input_hash):
    if not (output / "result.json").exists() or not (output / "verify.json").exists():
        raise DomainError("OUTPUT_INVALID", "Blender出力manifestがありません。")
    result, verified = read_json(output / "result.json"), read_json(output / "verify.json")
    expected_ids = sorted(o.object_id for o in scene.objects)
    if result["input_hash"] != input_hash or verified["input_hash"] != input_hash:
        raise DomainError("OUTPUT_INVALID", "render input hash mismatch")
    if result["object_ids"] != expected_ids or verified["object_ids"] != expected_ids:
        raise DomainError("OUTPUT_INVALID", ".blend object ID mismatch")
    if verified["camera_ids"] != sorted(c.camera_id for c in scene.cameras) or verified["state_id"] != state.state_id:
        raise DomainError("OUTPUT_INVALID", ".blend camera/state mismatch")
    for obj in scene.objects:
        if obj.template_id == "window" and state.objects[obj.object_id].presence == "placed":
            if verified.get("openings", {}).get(obj.object_id) != {"rows": obj.opening.rows, "columns_per_row": obj.opening.columns_per_row}:
                raise DomainError("OUTPUT_INVALID", ".blend window partition mismatch")
    if len(result["artifacts"]) != 4 or not result["input_unchanged"]:
        raise DomainError("OUTPUT_INVALID", "four unchanged-state renders required")
    expected = sorted(r.reference_id for r in scene.references if r.surface_mapping and
                      state.objects[r.owner_object_id].presence == "placed" and
                      state.objects[r.owner_object_id].variant_id in r.applicable_variants)
    if verified.get("surface_reference_ids", []) != expected or result.get("surface_reference_ids", []) != expected:
        raise DomainError("OUTPUT_INVALID", "surface reference links changed or were not rendered")
    if verified.get("object_reference_ids", {}) != {o.object_id:o.reference_ids for o in scene.objects}:
        raise DomainError("OUTPUT_INVALID", "object/detail reference IDs changed on reopen")
    for texture in result.get("surfaces", []):
        path = (output / texture["path"]).resolve()
        if not path.is_relative_to(output.resolve()) or file_hash(path) != texture["sha256"]:
            raise DomainError("OUTPUT_INVALID", "surface texture checksum/path mismatch")
    for artifact in result["artifacts"]:
        path = output / artifact["path"]
        if path.parent.resolve() != output.resolve() or file_hash(path) != artifact["sha256"]:
            raise DomainError("OUTPUT_INVALID", "PNG checksum/path mismatch")
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG" or image.width < 64 or max(ImageStat.Stat(image).stddev) < 2:
                raise DomainError("OUTPUT_INVALID", "blank/invalid PNG")

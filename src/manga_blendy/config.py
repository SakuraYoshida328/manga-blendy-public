import os
from pathlib import Path

from .storage import atomic_json, read_json

APP_ROOT = Path(os.environ.get("MANGA_BLENDY_HOME", Path(__file__).resolve().parents[2])).resolve()
DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"


def load_config():
    path = APP_ROOT / "config.json"
    saved = read_json(path) if path.exists() else {}
    return {"blender_path": saved.get("blender_path", DEFAULT_BLENDER),
            "api_model": saved.get("api_model", os.environ.get("MANGA_BLENDY_MODEL", "")),
            "blender_timeout": 600, "api_timeout": 180, "render_samples": 16,
            'operation_mode':'chatgpt_plan','setup_completed':bool(saved.get('setup_completed',False))}


def save_config(blender_path: str, api_model: str):
    path=APP_ROOT/'config.json';saved=read_json(path) if path.exists() else {}
    saved.update(blender_path=blender_path,api_model=api_model)
    atomic_json(path,saved)

def save_setup(options):
    from .diagnostics import SetupOptions
    options=SetupOptions.model_validate(options)
    path=APP_ROOT/'config.json';saved=read_json(path) if path.exists() else {}
    saved.update(operation_mode=options.operation_mode,blender_path=options.blender_path,setup_completed=True)
    atomic_json(path,saved)

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
from filelock import FileLock, Timeout

from .analysis import ObservationProposal, PROMPT_VERSION
from .blender_adapter import render, terminate_owned
from .config import APP_ROOT, load_config
from .domain import DomainError, SceneRevision, SceneState, content_hash, now, uid
from .planning import make_plan
from .storage import Store, atomic_json, read_json, safe_path

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def update_job(path, **values):
    with FileLock(str(path.with_suffix(".lock"))):
        data = read_json(path)
        data.update(values, updated_at=now())
        atomic_json(path, data)
        return data


def alive(pid, created):
    try:
        return abs(psutil.Process(pid).create_time()-created) < .1
    except (psutil.Error, TypeError):
        return False


class JobManager:
    """One persisted queue, one independent worker. No bpy or paid calls in web process."""
    def __init__(self, store: Store):
        self.store = store
        self.stop_event = threading.Event()
        self.thread = None
        self.process = None

    def paths(self):
        return sorted(self.store.root.glob("project_*/jobs/job_*/job.json"), key=lambda p: p.stat().st_mtime)

    def start(self):
        # Surviving workers keep running after a web restart; PID identity prevents unrelated kills.
        for path in self.paths():
            job = read_json(path)
            if job["status"] not in TERMINAL | {"queued"} and not alive(job.get("worker_pid"), job.get("worker_created")):
                update_job(path, status="interrupted", error={"code": "INTERRUPTED", "message": "ワーカーが終了しています。保存済み段階から再開できます。"})
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def enqueue(self, pid: str, kind="analyze", scene=None, states=None, expected=None, observation=None, render_request=None,inputs_ready=True):
        root = self.store.project(pid)
        manifest = self.store.manifest(pid)
        jid = uid("job")
        directory = root / "jobs" / jid
        directory.mkdir(parents=True)
        config = load_config()
        key = content_hash({"source": manifest.assets[0].sha256, "answers": manifest.answers,
                            "prompt": PROMPT_VERSION, "model": config["api_model"], "kind": kind,
                            "scene": scene.model_dump(mode="json") if scene else None,
                            "states": [s.model_dump(mode="json") for s in states] if states else None,
                            "observation": observation.model_dump(mode="json") if observation else None,
                            "render_request": render_request})
        if observation is not None:
            atomic_json(directory / "observations.json", observation)
        if render_request is not None:
            atomic_json(directory / "render_options.json", render_request)
        if scene:
            atomic_json(directory / "candidate_scene.json", scene)
            atomic_json(directory / "candidate_states.json", [s.model_dump(mode="json") for s in states])
            atomic_json(directory / "assets.json", [])
        data = {"schema_version": 1, "job_id": jid, "project_id": pid, "kind": kind,
                "status": "queued", "stage": "queued", "created_at": now(), "updated_at": now(),
                "input_hash": key, "base_revision_id": expected if expected is not None else manifest.active_revision_id,
                "worker_pid": None, "worker_created": None, "blender_pid": None, "blender_created": None,
                "attempts": 0, "api_attempts": 0, "error": None, "elapsed_seconds": None,
                "config": config}
        data['inputs_ready']=inputs_ready
        atomic_json(directory / "job.json", data)
        return jid

    def job(self, pid, jid):
        return read_json(safe_path(self.store.project(pid), f"jobs/{jid}/job.json"))

    def cancel(self, pid, jid):
        root = safe_path(self.store.project(pid), f"jobs/{jid}")
        job = read_json(root / "job.json")
        if job["status"] in TERMINAL:
            return
        (root / "cancel.request").touch()
        if (job["kind"] == "render_image" or job.get('mcp_active')) and job["status"] != "queued" and alive(job.get("worker_pid"), job.get("worker_created")):
            update_job(root / "job.json", status="cancelling", error={"code":"CANCELLING", "message":"停止要求を記録しました。MCPの現在の1枚を完了し、Blenderのシーンを復元して停止します。"})
            return
        if job["status"] != "queued" and job.get("worker_pid") != os.getpid() and alive(job.get("worker_pid"), job.get("worker_created")):
            terminate_owned(job["worker_pid"], job["worker_created"])
        update_job(root / "job.json", status="cancelled", error={"code": "CANCELLED", "message": "所有するワーカーと子プロセスを停止しました。"})

    def resume(self, pid, jid, allow_paid_retry=False):
        directory = safe_path(self.store.project(pid), f"jobs/{jid}")
        job = read_json(directory / "job.json")
        if job["status"] not in {"failed", "interrupted", "cancelled"}:
            raise DomainError("JOB_INVALID", "failed/interrupted job required")
        if job["api_attempts"] and not (directory / "observations.json").exists() and not allow_paid_retry:
            raise DomainError("AI_RETRY_CONFIRM", "API再送は追加課金の可能性があります。明示して再開してください。")
        (directory / "cancel.request").unlink(missing_ok=True)
        config = load_config()
        if (directory / "observations.json").exists():
            config["api_model"] = job["config"]["api_model"]
        update_job(directory / "job.json", status="queued", error=None, config=config,
                   worker_pid=None, worker_created=None, blender_pid=None, blender_created=None)

    def loop(self):
        while not self.stop_event.wait(.5):
            try:
                paths = self.paths()
                jobs = [(p, read_json(p)) for p in paths]
                for p, j in jobs:
                    if j["status"] not in TERMINAL | {"queued"} and not alive(j.get("worker_pid"), j.get("worker_created")):
                        update_job(p, status="interrupted", error={"code": "INTERRUPTED", "message": "ワーカーが終了しました。保存済み段階から再開できます。"})
                if any(j["status"] not in TERMINAL | {"queued"} and alive(j.get("worker_pid"), j.get("worker_created")) for _, j in jobs):
                    continue
                queued = next(((p, j) for p, j in jobs if j["status"] == "queued" and j.get('inputs_ready',True)), None)
                if not queued:
                    continue
                path, job = queued
                with FileLock(str(self.store.root / ".dispatch.lock"), timeout=.1):
                    if read_json(path)["status"] != "queued":
                        continue
                    update_job(path, status="starting")
                    log = (path.parent / "worker.log").open("ab")
                    env = os.environ.copy()
                    env["MANGA_BLENDY_HOME"] = str(APP_ROOT)
                    process = subprocess.Popen([sys.executable, "-m", "manga_blendy.jobs", str(self.store.root),
                                                job["project_id"], job["job_id"]], stdout=log, stderr=subprocess.STDOUT,
                                               env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    log.close()
                    self.process = process
                    try:
                        update_job(path, worker_pid=process.pid, worker_created=psutil.Process(process.pid).create_time())
                    except psutil.Error:
                        pass
            except (OSError, Timeout):
                continue


def run_job(store_root, pid, jid):
    store = Store(Path(store_root))
    root = store.project(pid)
    directory = safe_path(root, f"jobs/{jid}")
    path = directory / "job.json"
    started = time.monotonic()
    with FileLock(str(store.root / ".worker.lock")):
        job = read_json(path)
        update_job(path, worker_pid=os.getpid(), worker_created=psutil.Process().create_time(),
                   attempts=job["attempts"]+1)
        cancelled = lambda: (directory / "cancel.request").exists()
        def stage(name):
            if cancelled():
                raise DomainError("CANCELLED", "処理を停止しました。")
            update_job(path, status=name, stage=name)
        try:
            manifest = store.manifest(pid)
            if not (directory / "candidate_scene.json").exists():
                stage("analyzing")
                if (directory / "observations.json").exists():
                    proposal = ObservationProposal.model_validate(read_json(directory / "observations.json"))
                else:
                    raise DomainError("AI_UNAVAILABLE", "Codex/ChatGPTで解析したJSONを元画像と一緒に取り込んでください。APIキーによる別課金は使用しません。")
                stage("planning")
                scene, states, assets = make_plan(manifest, root, proposal, jid)
                atomic_json(directory / "candidate_scene.json", scene)
                atomic_json(directory / "candidate_states.json", [s.model_dump(mode="json") for s in states])
                atomic_json(directory / "assets.json", [a.model_dump(mode="json") for a in assets])
            scene = SceneRevision.model_validate(read_json(directory / "candidate_scene.json"))
            states = [SceneState.model_validate(s) for s in read_json(directory / "candidate_states.json")]
            state = states[0] if job["kind"] in {"render", "render_image",'exterior'} else next(s for s in states if s.state_id == scene.source_state_id)
            if scene.readiness != "reference_only":
                stage("building")
                stage("rendering")
                if job['kind']=='exterior':
                    from .exterior import update_saved_exterior
                    verified=update_saved_exterior(scene,state,directory,job['config'],cancelled,
                        lambda p,c:update_job(path,blender_pid=p,blender_created=c))
                    from .domain import SavedGeometry
                    scene.saved_geometry=SavedGeometry(relative_path=f'jobs/{jid}/output/lightweight.blend',sha256=verified['output_sha256'])
                    atomic_json(directory/'candidate_scene.json',scene)
                    from .mcp_render import render_saved
                    from .manga_style import MangaRenderOptions
                    update_job(path,mcp_active=True)
                    render_saved(scene,state,directory,job['config'],{'stage':'reference_validation',
                        'camera_id':read_json(directory/'exterior_update.json')['camera_id'],
                        'render':MangaRenderOptions(focus_mode='balanced',resolution=(640,480)).model_dump(mode='json')},cancelled)
                elif job["kind"] == "render_image":
                    from .mcp_render import render_saved
                    options=read_json(directory / "render_options.json")
                    update_job(path,mcp_active=True)
                    result=render_saved(scene, state, directory, job["config"], options, cancelled)
                    if options.get('workflow','one_stage')=='two_stage':
                        from .finishing import prepare_handoff
                        assets=[a.model_dump(mode='json') for a in manifest.assets]+read_json(directory/'assets.json')
                        # A simplified structural diagram must not replace the original appearance reference.
                        style=next((a for a in assets if a.get('role')=='normalized' and a.get('source_asset_id')==scene.source_asset_id),None)
                        if style is None:style=next((a for a in assets if a['asset_id']==scene.source_asset_id),None)
                        if style is None:raise DomainError('FINISH_REFERENCE_REQUIRED','正式マスターの画風参照が未登録です。')
                        prepare_handoff(directory,result,scene,state,safe_path(root,style['relative_path']))
                        update_job(path,workflow_status='awaiting_external_edit',output_ready=False)
                    else:update_job(path,workflow_status='one_stage_complete',output_ready=True)
                else:
                    if scene.saved_geometry is not None:
                        from .mcp_render import render_saved_views
                        update_job(path,mcp_active=True)
                        render_saved_views(scene,state,directory,job['config'],cancelled)
                    else:
                        render(scene, state, directory, job["config"], cancelled,
                               lambda p, c: update_job(path, blender_pid=p, blender_created=c))
                        geometry=directory/'output/lightweight.blend'
                        if geometry.is_file():
                            from .domain import SavedGeometry
                            from .storage import file_hash
                            scene.saved_geometry=SavedGeometry(relative_path=geometry.relative_to(root).as_posix(),sha256=file_hash(geometry))
                            atomic_json(directory/'candidate_scene.json',scene)
            stage("validating")
            atomic_json(directory / "validation.json", {"readiness": scene.readiness,
                        "projection": scene.projection_report, "unresolved": scene.unresolved,
                        "rendered": scene.readiness != "reference_only", "at": now()})
            if job["kind"] in {"render", "render_image"} and (directory / "output/result.json").exists():
                import shutil
                result = read_json(directory / "output/result.json")
                destination = safe_path(root, f"renders/{scene.revision_id}/{state.state_id}/{result['input_hash']}")
                if not destination.exists():
                    # A cached 3D base is independent of an optional edited candidate/review.
                    shutil.copytree(directory / "output", destination,ignore=shutil.ignore_patterns('finish'))
            update_job(path, status="succeeded", stage="succeeded", error=None,mcp_active=False, elapsed_seconds=round(time.monotonic()-started, 2))
        except DomainError as exc:
            update_job(path, status="cancelled" if exc.code == "CANCELLED" else "failed",mcp_active=False,
                       error={"code": exc.code, "message": str(exc)}, elapsed_seconds=round(time.monotonic()-started, 2))
        except Exception as exc:
            import traceback
            traceback.print_exc()
            update_job(path, status="failed",mcp_active=False, error={"code": "WORKER_FAILED", "message": f"{type(exc).__name__}: {exc}"},
                       elapsed_seconds=round(time.monotonic()-started, 2))


if __name__ == "__main__":
    run_job(*sys.argv[1:4])

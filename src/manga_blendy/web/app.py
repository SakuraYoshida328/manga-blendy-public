from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..blender_adapter import diagnose
from ..config import APP_ROOT, load_config, save_config
from ..domain import DomainError, SceneRevision, SceneState, new_state
from ..jobs import JobManager
from ..revisions import revise
from ..storage import MAX_UPLOAD, Store, read_json, safe_path
from ..observation_import import MAX_OBSERVATION_BYTES, ObservationPackage, import_observation
from ..manga_style import BackgroundRenderRequest
from ..material_catalog import MATERIAL_LABELS, display_components
from ..finishing import (CHECKS, FinishReview, import_candidate, review_candidate, verify_handoff,
    export_handoff,publish_approved_output)
from ..diagnostics import SetupOptions,capabilities,check_environment
from ..config import save_setup
from ..integration import find_backgrounds,get_master,request_background,export_image_bundle
from ..jobs import update_job

WEB_ROOT = Path(__file__).parent


def create_app(store_root=None, start_workers=True):
    store = Store(Path(store_root or APP_ROOT / "projects"))
    manager = JobManager(store)
    token = secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
    from ..organization import ROLE_LABELS
    templates.env.globals["management_role_labels"] = ROLE_LABELS
    templates.env.globals["material_labels"] = MATERIAL_LABELS
    templates.env.globals["material_components"] = display_components
    templates.env.globals["camera_label"] = lambda scene, image: next(
        (c.label for c in scene.cameras if c.camera_id == Path(image).stem), Path(image).stem)

    @asynccontextmanager
    async def lifespan(app):
        if start_workers:
            manager.start()
        yield
        manager.close()

    app = FastAPI(title="Manga Blendy", lifespan=lifespan)
    app.state.store, app.state.manager = store, manager
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")

    async def protect(request: Request):
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            raise DomainError("REQUEST_INVALID", "origin mismatch")
        received = request.headers.get("x-manga-token")
        if not received and not request.headers.get("content-type", "").startswith("application/json"):
            received = (await request.form()).get("token")
        if not received or not secrets.compare_digest(str(received), token) or request.cookies.get("mb_session") != token:
            raise DomainError("REQUEST_INVALID", "画面を再読み込みしてください。")

    def page(request, name, **context):
        response = templates.TemplateResponse(request=request, name=name, context={"token": token, **context})
        response.set_cookie("mb_session", token, httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=400)

    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": {"code": "INPUT_INVALID", "message": str(exc)}}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        config = load_config()
        blender_status = "実行ファイルあり（バージョンは処理開始時に確認）" if Path(config["blender_path"]).is_file() else "実行ファイルが見つかりません"
        return page(request, "home.html", projects=store.list_projects(), config=config,
                    blender_status=blender_status)

    @app.get("/new")
    async def new(request: Request):
        return page(request, "new.html")

    @app.get('/setup')
    async def setup_page(request: Request):
        return page(request,'setup.html',config=load_config(),capabilities=capabilities())

    @app.post('/api/setup/diagnose',dependencies=[Depends(protect)])
    async def setup_diagnose(request: Request):
        options=SetupOptions.model_validate(await request.json())
        return await run_in_threadpool(check_environment,options.blender_path)

    @app.post('/setup',dependencies=[Depends(protect)])
    async def setup_save(blender_path: str=Form(...),operation_mode: str=Form('chatgpt_plan')):
        options=SetupOptions(blender_path=blender_path,operation_mode=operation_mode)
        save_setup(options)
        return RedirectResponse('/',303)

    @app.get('/capabilities')
    async def capability_contract():return capabilities()

    @app.get('/api/backgrounds')
    async def backgrounds(q: str=''):return {'backgrounds':find_backgrounds(store,q)}

    @app.get('/api/backgrounds/{pid}/master')
    async def master_packet(pid: str,state_id: str|None=None):return get_master(store,pid,state_id)

    @app.post('/api/backgrounds/{pid}/render',dependencies=[Depends(protect)])
    async def integration_render(request: Request,pid: str):
        body=await request.json();sid=body.pop('state_id',None)
        return request_background(store,manager,pid,sid,body)

    @app.post('/api/backgrounds/{pid}/jobs/{jid}/export-image',dependencies=[Depends(protect)])
    async def integration_export(request: Request,pid: str,jid: str):
        directory=export_image_bundle(store,pid,jid,(await request.json()).get('camera_id'))
        relative=directory.relative_to(store.project(pid)).as_posix()
        return {'image_url':f'/projects/{pid}/file/{relative}/background.png',
            'metadata_url':f'/projects/{pid}/file/{relative}/background.json'}

    @app.post('/api/projects/{pid}/improvements',dependencies=[Depends(protect)])
    async def propose_improvement(request: Request,pid: str):
        from ..maintenance import propose
        return propose(store,pid,await request.json())

    @app.post('/api/projects/{pid}/improvements/{iid}/approve',dependencies=[Depends(protect)])
    async def approve_improvement(request: Request,pid: str,iid: str):
        from ..maintenance import approve
        body=await request.json()
        if body.get('approved') is not True:raise DomainError('APPROVAL_REQUIRED','利用者の明示承認が必要です。')
        return approve(store,pid,iid,body.get('expected_revision'))

    @app.post('/api/projects/{pid}/exterior',dependencies=[Depends(protect)])
    async def replace_exterior(pid: str,image: UploadFile,camera_id: str|None=Form(None)):
        from ..exterior import prepare_exterior
        return prepare_exterior(store,manager,pid,await image.read(MAX_UPLOAD+1),camera_id)

    @app.get("/analysis-contract")
    async def analysis_contract():
        from ..analysis import SYSTEM_PROMPT
        return {"contract": "manga-blendy.observation", "schema": ObservationPackage.model_json_schema(),
                "instructions": SYSTEM_PROMPT,
                "image_coordinates": "EXIF補正後の元解像度画像で0〜1の正規化座標",
                "source_hash": "選択する元ファイルのSHA256（小文字16進数）"}

    @app.get("/background-contract")
    async def background_contract():
        return {"contract":"manga-blendy.background-request","schema":BackgroundRenderRequest.model_json_schema(),
                "endpoint":"POST /api/projects/{project_id}/states/{state_id}/render-image",
                "session_required":True,"output":"one grayscale PNG and result.json; optional external finish handoff",
                "finish_automatic_generation":False,"finish_review_required":True,"paid_api_calls":False}

    @app.post("/settings", dependencies=[Depends(protect)])
    async def settings(blender_path: str = Form(...)):
        diagnose(blender_path)
        save_config(blender_path, "")
        return RedirectResponse("/", 303)

    @app.post("/projects", dependencies=[Depends(protect)])
    async def create(image: UploadFile, observation_file: UploadFile, title: str = Form(...), place: str = Form("部屋"),
                     usage: str = Form("漫画背景"), policy: str = Form("preserve"),
                     fixed: str = Form("architecture,furniture"), temporal: str = Form("same_scene"),
                     context: str = Form(""), narrative: str = Form(""), measure: str = Form(""),
                     measure_object: str = Form(""), measure_width_m: float | None = Form(None),
                     max_detailed_movable: int = Form(12), max_geometry_units: int = Form(50000)):
        data = await image.read(MAX_UPLOAD+1)
        proposal = import_observation(data, await observation_file.read(MAX_OBSERVATION_BYTES+1))
        from ..domain import ManagementPolicy
        policy_settings = ManagementPolicy(max_detailed_movable=max_detailed_movable,
                                           max_geometry_units=max_geometry_units)
        if measure_width_m is not None and not .02 <= measure_width_m <= 100:
            raise DomainError("INPUT_INVALID", "実寸の幅は0.02〜100mで指定してください。")
        manifest = store.create(title, data, dict(place=place, usage=usage, policy=policy, fixed=fixed,
                                temporal=temporal, context=context, narrative=narrative, measure=measure,
                                measure_object=measure_object, measure_width_m=measure_width_m,
                                management_policy=policy_settings.model_dump(mode="json")))
        jid = manager.enqueue(manifest.project_id, kind="import", observation=proposal)
        return RedirectResponse(f"/projects/{manifest.project_id}/jobs/{jid}", 303)

    @app.post("/import", dependencies=[Depends(protect)])
    async def import_project(archive: UploadFile):
        pid = store.import_zip(await archive.read(500*1024*1024+1))
        return RedirectResponse(f"/projects/{pid}", 303)

    @app.get("/projects/{pid}/jobs/{jid}")
    async def progress(request: Request, pid: str, jid: str):
        return page(request, "progress.html", manifest=store.manifest(pid), job=manager.job(pid, jid))

    @app.get("/api/projects/{pid}/jobs/{jid}")
    async def status(pid: str, jid: str):
        job = manager.job(pid, jid)
        result={k: v for k, v in job.items() if k not in {"config", "worker_pid", "worker_created", "blender_pid", "blender_created"}}
        result['finish_ready']=(safe_path(store.project(pid),f'jobs/{jid}/output/finish/request.json')).is_file()
        if result['finish_ready']:
            folder=safe_path(store.project(pid),f'jobs/{jid}/output/finish')
            candidates=[read_json(p) for p in folder.glob('finish_*.json')]
            result['workflow_status']='approved_output' if any(c.get('status')=='approved_output' for c in candidates) else 'awaiting_user_review' if any(c.get('status')=='awaiting_user_review' for c in candidates) else 'awaiting_external_edit'
            result['output_ready']=result['workflow_status']=='approved_output'
        return result

    @app.post("/api/projects/{pid}/jobs/{jid}/cancel", dependencies=[Depends(protect)])
    async def cancel(pid: str, jid: str):
        manager.cancel(pid, jid)
        return {"ok": True}

    @app.post("/api/projects/{pid}/jobs/{jid}/resume", dependencies=[Depends(protect)])
    async def resume(request: Request, pid: str, jid: str):
        body = await request.json()
        manager.resume(pid, jid, body.get("allow_paid_retry", False))
        return {"ok": True}

    def candidate(pid, jid):
        job = manager.job(pid, jid)
        if job["status"] != "succeeded":
            raise DomainError("JOB_INVALID", "処理完了後に確認できます。")
        directory = safe_path(store.project(pid), f"jobs/{jid}")
        scene = SceneRevision.model_validate(read_json(directory / "candidate_scene.json"))
        states = [SceneState.model_validate(s) for s in read_json(directory / "candidate_states.json")]
        return directory, scene, states, job

    @app.get("/projects/{pid}/jobs/{jid}/review")
    async def review(request: Request, pid: str, jid: str):
        directory, scene, states, job = candidate(pid, jid)
        manifest = store.manifest(pid)
        assets = {a.asset_id: a.model_dump() for a in manifest.assets}
        for a in read_json(directory / "assets.json"):
            assets[a["asset_id"]] = a
        artifacts = read_json(directory / "output/result.json")["artifacts"] if (directory / "output/result.json").exists() else []
        result=read_json(directory/'output/result.json') if (directory/'output/result.json').exists() else {}
        finished=result.get('finished_previews',[])
        return page(request, "packet.html", manifest=manifest, scene=scene, state=states[0],
                    assets=assets, job=job, images=[f"jobs/{jid}/output/{a['path']}" for a in (finished or artifacts)],
                    finishing_preview=bool(finished),states=states, review=True,
                    source=manifest.assets[1].relative_path)

    @app.post("/api/projects/{pid}/jobs/{jid}/adopt", dependencies=[Depends(protect)])
    async def adopt(request: Request, pid: str, jid: str):
        body = await request.json()
        manifest = store.adopt(pid, jid, body.get("expected_revision"), body.get("simplified", False))
        return {"project_id": manifest.project_id}

    @app.post("/api/projects/{pid}/jobs/{jid}/correct", dependencies=[Depends(protect)])
    async def correct(request: Request, pid: str, jid: str):
        _, scene, states, job = candidate(pid, jid)
        result, snapshots = revise(scene, states, (await request.json())["corrections"])
        next_job = manager.enqueue(pid, "revise", result, snapshots, job["base_revision_id"])
        # Preserve crop assets created by the parent candidate until explicit adoption.
        from ..storage import atomic_json
        parent = safe_path(store.project(pid), f"jobs/{jid}/assets.json")
        atomic_json(safe_path(store.project(pid), f"jobs/{next_job}/assets.json"), read_json(parent))
        return {"job_id": next_job}

    @app.get("/projects/{pid}")
    async def packet(request: Request, pid: str, state_id: str | None = None):
        manifest = store.manifest(pid)
        if not manifest.active_revision_id:
            pending = sorted(store.project(pid).glob("jobs/*/job.json"))
            return page(request, "pending.html", manifest=manifest, jobs=[read_json(p) for p in pending])
        state = store.state(pid, state_id or manifest.active_state_id)
        scene = store.revision(pid, state.revision_id)
        assets = {a.asset_id: a.model_dump() for a in manifest.assets}
        directories = sorted(store.project(pid).glob(f"renders/{scene.revision_id}/{state.state_id}/*/result.json"), key=lambda p: p.stat().st_mtime)
        images = []
        finishing_preview=False
        if directories:
            output = directories[-1].parent
            result=read_json(output/'result.json');finished=result.get('finished_previews',[])
            if (output/'approved_finish.json').is_file():
                approved=read_json(output/'approved_finish.json')
                if approved['base_input_hash']==result['input_hash']:
                    from ..storage import file_hash
                    if file_hash(safe_path(output,approved['artifact']['path']))!=approved['artifact']['sha256']:
                        raise DomainError('OUTPUT_INVALID','承認画像の保存内容が変わっています。')
                    finished=[approved['artifact']]
            finishing_preview=bool(finished)
            images = [(output / a["path"]).relative_to(store.project(pid)).as_posix()
                      for a in (finished or result['artifacts'])]
        states = [SceneState.model_validate(read_json(p)) for p in store.project(pid).glob("states/*.json")]
        return page(request, "packet.html", manifest=manifest, scene=scene, state=state, assets=assets,
                    images=images,finishing_preview=finishing_preview, states=states, review=False, job=None,
                    source=manifest.assets[1].relative_path)

    @app.post("/api/projects/{pid}/states", dependencies=[Depends(protect)])
    async def state_change(request: Request, pid: str):
        body = await request.json()
        parent = store.state(pid, body["parent_state_id"])
        scene = store.revision(pid, parent.revision_id)
        state = new_state(scene, parent, body.get("story_time", "次の場面"), body.get("commands", []), body.get("story_scene_id"))
        store.save_state(pid, state, body["expected_state_id"])
        return {"state_id": state.state_id}

    @app.post("/api/projects/{pid}/states/{sid}/render", dependencies=[Depends(protect)])
    async def render_state(pid: str, sid: str):
        state = store.state(pid, sid)
        jid = manager.enqueue(pid, "render", store.revision(pid, state.revision_id), [state])
        return {"job_id": jid}

    @app.post("/api/projects/{pid}/states/{sid}/render-image", dependencies=[Depends(protect)])
    async def render_image(request: Request, pid: str, sid: str):
        options = BackgroundRenderRequest.model_validate(await request.json())
        state = store.state(pid, sid)
        scene = store.revision(pid, state.revision_id)
        if scene.readiness == "reference_only":
            raise DomainError("SAVED_GEOMETRY_REQUIRED", "参照資料のみの状態です。保存3Dの確認が必要です。")
        if scene.saved_geometry is None:
            raise DomainError("SAVED_GEOMETRY_REQUIRED", "この改訂には漫画仕上げ用3Dが未登録です。グレーの模型へ自動切替しません。")
        if options.camera_id not in {c.camera_id for c in scene.cameras}:
            raise DomainError("CAMERA_INVALID", "登録済みのカメラを選んでください。")
        jid = manager.enqueue(pid, "render_image", scene, [state], render_request=options.model_dump(mode="json"))
        return {"job_id": jid}

    def finish_folder(pid,jid):
        job=manager.job(pid,jid)
        if job['status']!='succeeded' or job['kind']!='render_image':
            raise DomainError('JOB_INVALID','構造画像の出力後に仕上げを確認できます。')
        folder=safe_path(store.project(pid),f'jobs/{jid}/output/finish')
        if not (folder/'request.json').is_file():
            raise DomainError('FINISH_REQUEST_REQUIRED','この出力は一段階です。二段階試験を指定してください。')
        return folder

    @app.get('/projects/{pid}/jobs/{jid}/finish')
    async def finish_page(request: Request,pid: str,jid: str):
        folder=finish_folder(pid,jid);handoff=verify_handoff(folder)
        candidates=[read_json(p) for p in sorted(folder.glob('finish_*.json'))]
        return page(request,'finish.html',manifest=store.manifest(pid),jid=jid,
                    handoff=handoff,candidates=candidates,checks=CHECKS)

    @app.get('/projects/{pid}/jobs/{jid}/finish/handoff')
    async def finish_download(pid: str,jid: str):
        path=export_handoff(finish_folder(pid,jid))
        return FileResponse(path,filename='manga-blendy-finish-handoff.zip')

    @app.post('/api/projects/{pid}/jobs/{jid}/finish/import',dependencies=[Depends(protect)])
    async def finish_import(pid: str,jid: str,image: UploadFile,provider: str=Form('codex_builtin'),model_label: str=Form('unknown')):
        result=import_candidate(finish_folder(pid,jid),await image.read(MAX_UPLOAD+1),provider,model_label)
        update_job(safe_path(store.project(pid),f'jobs/{jid}/job.json'),
            workflow_status='awaiting_user_review' if result['status']=='awaiting_user_review' else 'awaiting_external_edit',output_ready=False)
        return {'candidate_id':result['candidate_id'],'status':result['status']}

    @app.post('/api/projects/{pid}/jobs/{jid}/finish/review',dependencies=[Depends(protect)])
    async def finish_review(request: Request,pid: str,jid: str):
        result=review_candidate(finish_folder(pid,jid),FinishReview.model_validate(await request.json()))
        if result['status']=='approved_output':publish_approved_output(store.project(pid),finish_folder(pid,jid))
        update_job(safe_path(store.project(pid),f'jobs/{jid}/job.json'),
            workflow_status='approved_output' if result['status']=='approved_output' else 'awaiting_external_edit',
            output_ready=result['status']=='approved_output')
        return {'candidate_id':result['candidate_id'],'status':result['status']}

    @app.post("/api/projects/{pid}/revise", dependencies=[Depends(protect)])
    async def revise_accepted(request: Request, pid: str):
        body = await request.json()
        manifest = store.manifest(pid)
        if body["expected_revision"] != manifest.active_revision_id:
            raise DomainError("REVISION_CONFLICT", "reload project")
        scene = store.revision(pid, manifest.active_revision_id)
        states = [store.state(pid, sid) for sid in dict.fromkeys([scene.source_state_id, scene.baseline_state_id])]
        result, snapshots = revise(scene, states, body["corrections"])
        return {"job_id": manager.enqueue(pid, "revise", result, snapshots, manifest.active_revision_id)}

    @app.post("/api/projects/{pid}/export", dependencies=[Depends(protect)])
    async def export(pid: str):
        path = store.export(pid)
        return {"url": f"/projects/{pid}/download/{path.parent.name}"}

    @app.get("/projects/{pid}/download/{eid}")
    async def download(pid: str, eid: str):
        path = safe_path(store.project(pid), f"exports/{eid}/package.zip")
        return FileResponse(path, filename="manga-blendy.zip")

    @app.get("/projects/{pid}/file/{relative:path}")
    async def file(pid: str, relative: str):
        root = store.project(pid)
        path = safe_path(root, relative)
        parts = Path(relative).parts
        allowed = ((parts[0] == "assets" and path.suffix.lower() in {".png", ".jpg", ".webp"}) or
                   (len(parts)>=4 and parts[:2]==('exports','images') and path.name in {'background.png','background.json'}) or
                   (parts[0] in {"renders", "revisions"} and path.suffix in {".png", ".blend", ".json"}) or
                   (len(parts) >= 4 and parts[0] == "jobs" and parts[2] == "output" and path.suffix == ".png") or
                   (len(parts)==5 and parts[0]=='jobs' and parts[2:4]==('output','finish') and path.name in {'request.json','edit_prompt.txt'}))
        if not allowed or not path.is_file():
            raise DomainError("PATH_INVALID", "requested artifact unavailable")
        return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})

    return app

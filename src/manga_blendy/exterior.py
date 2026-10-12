"""Independently replace a 2D exterior using a new reviewable revision."""
import io
from pathlib import Path
from PIL import Image,ImageOps
from .domain import DomainError,RoomSystems,uid
from .storage import MAX_UPLOAD,atomic_json,file_hash,image_asset,safe_path,read_json
from .blender_adapter import execute,diagnose

WORKER=Path(__file__).parent/'blender_worker/update_exterior.py'

def prepare_exterior(store,manager,pid,data,camera_id=None):
    if len(data)>MAX_UPLOAD:raise DomainError('IMAGE_INVALID','屋外画像は20MB以下にしてください。')
    manifest=store.manifest(pid)
    if not manifest.active_revision_id:raise DomainError('MASTER_REQUIRED','採用済みマスターが必要です。')
    scene=store.revision(pid,manifest.active_revision_id)
    if scene.saved_geometry is None:raise DomainError('SAVED_GEOMETRY_REQUIRED','保存3Dのマスターが必要です。')
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in {'PNG','JPEG','WEBP'} or max(im.size)>4096 or min(im.size)<64:
                raise DomainError('IMAGE_INVALID','64〜4096pxのPNG・JPEG・WebPを指定してください。')
            image=ImageOps.exif_transpose(im).convert('L')
    except (OSError,ValueError) as exc:
        if isinstance(exc,DomainError):raise
        raise DomainError('IMAGE_INVALID','屋外画像を読み込めません。') from exc
    camera_id=camera_id or scene.cameras[0].camera_id
    if camera_id not in {c.camera_id for c in scene.cameras}:raise DomainError('CAMERA_INVALID','登録済みカメラを選んでください。')
    root=store.project(pid);aid=uid('asset');relative=f'assets/exteriors/{aid}.png'
    path=safe_path(root,relative);path.parent.mkdir(parents=True,exist_ok=True);image.save(path)
    asset=image_asset(root,relative,'exterior')
    state_ids=list(dict.fromkeys([manifest.active_state_id,scene.source_state_id,scene.baseline_state_id]))
    states=[store.state(pid,sid).model_copy(deep=True) for sid in state_ids]
    new=scene.model_copy(deep=True);new.revision_id=uid('revision');mapping={s.state_id:uid('state') for s in states}
    for state in states:
        state.parent_state_id=None;state.state_id=mapping[state.state_id];state.revision_id=new.revision_id
    new.source_state_id=mapping[scene.source_state_id];new.baseline_state_id=mapping[scene.baseline_state_id]
    new.room_systems=new.room_systems or RoomSystems()
    new.room_systems.exterior_mode='source_image_only';new.room_systems.exterior_source_sha256=asset.sha256
    new.room_systems.exterior_packed_image='MB_Exterior_'+asset.asset_id
    new.room_systems.exterior_asset_id=asset.asset_id;new.room_systems.exterior_crop_rectangles=[]
    old_geometry=scene.saved_geometry.model_dump(mode='json');new.saved_geometry=None
    jid=manager.enqueue(pid,'exterior',new,states,manifest.active_revision_id,inputs_ready=False)
    directory=root/'jobs'/jid
    atomic_json(directory/'assets.json',[asset.model_dump(mode='json')])
    atomic_json(directory/'exterior_update.json',{'source_geometry':old_geometry,'asset':asset.model_dump(mode='json'),'camera_id':camera_id})
    from .jobs import update_job
    update_job(directory/'job.json',adopt_state_id=states[0].state_id,inputs_ready=True)
    return {'job_id':jid,'revision_id':new.revision_id}

def update_saved_exterior(scene,state,job_dir,config,cancelled,on_process):
    request=read_json(job_dir/'exterior_update.json');root=job_dir.parent.parent
    source=safe_path(root,request['source_geometry']['relative_path']);asset=safe_path(root,request['asset']['relative_path'])
    if file_hash(source)!=request['source_geometry']['sha256'] or file_hash(asset)!=request['asset']['sha256']:
        raise DomainError('OUTPUT_INVALID','保存3Dまたは屋外参照が変化しています。')
    diagnose(config['blender_path']);out=job_dir/'output';out.mkdir(exist_ok=True)
    payload={'source_blend':str(source),'source_sha256':file_hash(source),'image':str(asset),'image_sha256':file_hash(asset),
        'scene':scene.model_dump(mode='json'),'state':state.model_dump(mode='json'),'output_dir':str(out)}
    atomic_json(job_dir/'exterior_worker.json',payload)
    execute(config['blender_path'],job_dir/'exterior_worker.json',out,config['blender_timeout'],cancelled,on_process,entry=WORKER)
    verified=read_json(out/'exterior_update_result.json')
    if not verified['source_unchanged'] or verified['geometry_before']!=verified['geometry_after'] or not verified['cameras_unchanged']:
        raise DomainError('OUTPUT_INVALID','屋外交換によって室内構造が変化しました。')
    if file_hash(out/'lightweight.blend')!=verified['output_sha256']:
        raise DomainError('OUTPUT_INVALID','改訂3Dの保存内容が変化しました。')
    return verified

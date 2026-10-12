"""Small file/API interface for other apps; no editing of those apps or manuscripts."""
from pathlib import Path
import shutil
from .domain import DomainError,content_hash,now
from .storage import atomic_json,read_json,safe_path,file_hash
from .manga_style import BackgroundRenderRequest

def find_backgrounds(store,query=''):
    q=query.strip().casefold()
    return [{'background_id':p.project_id,'name':p.title,'revision_id':p.active_revision_id,'state_id':p.active_state_id}
        for p in store.list_projects() if not q or q in p.title.casefold() or q in p.project_id.casefold()]

def get_master(store,background_id,state_id=None):
    m=store.manifest(background_id)
    if not m.active_revision_id:raise DomainError('MASTER_REQUIRED','採用済みマスターがありません。')
    state=store.state(background_id,state_id or m.active_state_id)
    scene=store.revision(background_id,state.revision_id)
    return {'contract':'manga-blendy.master','background_id':background_id,'name':m.title,
        'scene':scene.model_dump(mode='json'),'state':state.model_dump(mode='json'),
        'state_scope':'saved state' if state_id else 'active state','coordinate_definition':scene.coordinate_definition}

def request_background(store,manager,background_id,state_id,request):
    options=BackgroundRenderRequest.model_validate(request)
    packet=get_master(store,background_id,state_id)
    from .domain import SceneRevision,SceneState
    scene=SceneRevision.model_validate(packet['scene']);state=SceneState.model_validate(packet['state'])
    if scene.saved_geometry is None or scene.readiness!='review_ready':
        raise DomainError('SAVED_GEOMETRY_REQUIRED','背景出力用の保存3Dを採用してください。')
    if options.camera_id not in {c.camera_id for c in scene.cameras}:
        raise DomainError('CAMERA_INVALID','登録済みカメラを選んでください。')
    jid=manager.enqueue(background_id,'render_image',scene,[state],render_request=options.model_dump(mode='json'))
    return {'background_id':background_id,'job_id':jid}

def export_image_bundle(store,background_id,job_id,camera_id=None):
    """Return an unscaled PNG plus relative camera/state/provenance JSON."""
    root=store.project(background_id);job=safe_path(root,f'jobs/{job_id}')
    job_data=read_json(job/'job.json')
    if job_data['status']!='succeeded':raise DomainError('OUTPUT_REQUIRED','撮影完了後に書き出せます。')
    result=read_json(job/'output/result.json')
    scene=read_json(job/'candidate_scene.json');states=read_json(job/'candidate_states.json')
    state=next((s for s in states if s['state_id']==result.get('state_id')),states[0])
    camera_id=camera_id or result.get('camera_id')
    cameras=[c for c in scene['cameras'] if c['camera_id']==camera_id]
    if len(cameras)!=1:raise DomainError('CAMERA_INVALID','出力するカメラIDを指定してください。')
    artifact=next((a for a in result['artifacts'] if a['camera_id']==camera_id),None)
    if artifact is None:raise DomainError('OUTPUT_REQUIRED','このカメラの出力がありません。')
    kind='structure';source=safe_path(job/'output',artifact['path']);expected=artifact['sha256'];finish_metadata=None
    if (job/'output/finish/request.json').is_file():
        from .finishing import approved_candidate
        candidate=approved_candidate(job/'output/finish')
        if candidate:
            source=safe_path(job/'output/finish',candidate['image']);expected=candidate['sha256']
            kind='approved_manga';finish_metadata={'provider':candidate['provider'],'model_label':candidate['model_label'],'review':candidate['review']}
        elif read_json(job/'render_options.json').get('workflow')=='two_stage':
            raise DomainError('FINISH_REVIEW_REQUIRED','二段階の仕上げ承認が未完了です。構造画像と完成画像を混同しません。')
    elif result.get('finished_previews'):
        approved=next((a for a in result['finished_previews'] if a['camera_id']==camera_id and a.get('status')=='user_approved'),None)
        if approved:
            source=safe_path(job/'output',approved['path']);expected=approved['sha256'];kind='approved_manga'
    if file_hash(source)!=expected:raise DomainError('OUTPUT_INVALID','保存画像のハッシュが一致しません。')
    key=content_hash({'job':job_id,'camera':camera_id,'png':expected,'kind':kind})
    directory=safe_path(root,f'exports/images/{key}');directory.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,directory/'background.png')
    from PIL import Image
    with Image.open(source) as image:size=list(image.size)
    payload={'contract':'manga-blendy.image-bundle','schema_version':1,'background_id':background_id,
        'revision_id':scene['revision_id'],'state_id':state['state_id'],'camera':cameras[0],
        'coordinate_definition':scene['coordinate_definition'],'object_states':state['objects'],
        'image':{'path':'background.png','sha256':expected,'resolution':size,'kind':kind},
        'drawing_profile':result.get('profile'),'finish':finish_metadata,'generated_at':now(),
        'layers':'flattened PNG; independent ink/tone layers not implemented'}
    atomic_json(directory/'background.json',payload)
    return directory

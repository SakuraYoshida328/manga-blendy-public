"""Manga Blendy single-image renderer for the existing Blender MCP connection."""
from pathlib import Path
import json
import shutil
from PIL import Image,ImageStat
from .domain import DomainError,content_hash,validate_state,world_pose
import numpy as np
from .storage import safe_path,read_json,atomic_json,file_hash
from .mcp_transport import execute_file
from .manga_style import BackgroundRenderRequest

WORKER=Path(__file__).parent/'blender_worker/manga_saved.py'
SYSTEMS_WORKER=WORKER.parent/'room_systems.py'

def validate_saved_result(out,scene,state,camera_id,input_hash):
    data=read_json(out/'result.json')
    if data.get('input_hash')!=input_hash or data.get('revision_id')!=scene.revision_id or data.get('state_id')!=state.state_id or data.get('camera_id')!=camera_id:
        raise DomainError('OUTPUT_INVALID','背景出力の改訂・状態・カメラが一致しません。')
    if data.get('object_ids')!=sorted(o.object_id for o in scene.objects) or data.get('geometry_hash_before')!=data.get('geometry_hash_after') or not data.get('source_unchanged'):
        raise DomainError('OUTPUT_INVALID','保存3Dの構造・ID・状態が変化しました。')
    if data.get('presence')!={oid:v.presence for oid,v in state.objects.items()}:raise DomainError('OUTPUT_INVALID','小物の存在状態が一致しません。')
    if scene.room_systems and data.get('room_systems_validation',{}).get('status')!='passed':
        raise DomainError('OUTPUT_INVALID','照明・エアコン・カーテン・屋外参照の構造検査が未完了です。')
    for oid,item in state.objects.items():
        if item.presence!='placed':continue
        p,q=world_pose(scene,state,oid);pose=data.get('root_poses',{}).get(oid)
        if pose is None or not np.allclose(pose['position'],p,atol=1e-5):raise DomainError('OUTPUT_INVALID','背景の配置が場面データと一致しません。')
        expected=q.as_quat(scalar_first=True);actual=np.array(pose['quaternion'])
        if not (np.allclose(actual,expected,atol=1e-5) or np.allclose(actual,-expected,atol=1e-5)):
            raise DomainError('OUTPUT_INVALID','背景の向きが場面データと一致しません。')
    if len(data['artifacts'])!=1:raise DomainError('OUTPUT_INVALID','この要求は1視点のみです。')
    a=data['artifacts'][0];path=safe_path(out,a['path'])
    if file_hash(path)!=a['sha256']:raise DomainError('OUTPUT_INVALID','PNGが変化しました。')
    with Image.open(path) as im:
        if im.format!='PNG' or im.mode!='L' or list(im.size)!=data['profile']['resolution'] or max(ImageStat.Stat(im).stddev)<2:raise DomainError('OUTPUT_INVALID','白黒の背景PNGが無効です。')
    return data

def render_saved(scene,state,job_dir,config,options,cancelled=lambda:False,project_root=None):
    if cancelled():raise DomainError('CANCELLED','処理を停止しました。')
    validate_state(scene,state);request=BackgroundRenderRequest.model_validate(options)
    if scene.saved_geometry is None:raise DomainError('SAVED_GEOMETRY_REQUIRED','漫画仕上げ用の保存3Dがこの資料に登録されていません。')
    root=project_root or job_dir.parent.parent;source=safe_path(root,scene.saved_geometry.relative_path)
    if not source.is_file() or file_hash(source)!=scene.saved_geometry.sha256:raise DomainError('OUTPUT_INVALID','保存3Dのハッシュが一致しません。')
    camera=next((c for c in scene.cameras if c.camera_id==request.camera_id),None)
    if camera is None:raise DomainError('CAMERA_INVALID','登録済みカメラを選んでください。')
    profile=request.render.resolved();out=job_dir/'output';out.mkdir(exist_ok=True)
    key=content_hash({'scene':scene,'state':state,'camera':camera,'profile':profile,'geometry':scene.saved_geometry.sha256,'worker':file_hash(WORKER),'room_systems_worker':file_hash(SYSTEMS_WORKER),'samples':config['render_samples']})
    if (out/'result.json').is_file() and read_json(out/'result.json').get('input_hash')==key:
        return validate_saved_result(out,scene,state,request.camera_id,key)
    cache=safe_path(root,f'renders/{scene.revision_id}/{state.state_id}/{key}')
    if (cache/'result.json').is_file():
        data=validate_saved_result(cache,scene,state,request.camera_id,key)
        for a in data['artifacts']:
            target=safe_path(out,a['path']);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(safe_path(cache,a['path']),target)
        shutil.copyfile(cache/'result.json',out/'result.json')
        return validate_saved_result(out,scene,state,request.camera_id,key)
    payload={'source_blend':str(source),'source_blend_sha256':scene.saved_geometry.sha256,'scene':scene.model_dump(mode='json'),'state':state.model_dump(mode='json'),
        'camera':camera.model_dump(mode='json'),'profile':profile,'output_dir':str(out),'input_hash':key,'samples':config['render_samples']}
    atomic_json(job_dir/'manga_render_request.json',payload)
    runner=job_dir/'mcp_execute.py'
    runner.write_text('import sys,json,importlib\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(WORKER.parent))+')\nimport manga_saved\nimportlib.reload(manga_saved)\nmanga_saved.run(json.loads(Path('+repr(str(job_dir/'manga_render_request.json'))+').read_text(encoding="utf-8")))\n',encoding='utf-8')
    execute_file(runner,timeout=config['blender_timeout'])
    if cancelled():raise DomainError('CANCELLED','現在の1枚を完了してBlenderを復元し、処理を停止しました。')
    return validate_saved_result(out,scene,state,request.camera_id,key)


def render_saved_views(scene,state,job_dir,config,cancelled=lambda:False):
    """Four structural previews reuse the saved master, never rebuild its furniture."""
    from .manga_style import MangaRenderOptions
    root=job_dir.parent.parent;out=job_dir/'output';out.mkdir(exist_ok=True)
    results=[];artifacts=[]
    for camera in scene.cameras:
        options=BackgroundRenderRequest(stage='reference_validation',camera_id=camera.camera_id,
            render=MangaRenderOptions(focus_mode='balanced',resolution=camera.resolution))
        view=safe_path(job_dir,'views/'+options.camera_id);view.mkdir(parents=True,exist_ok=True)
        result=render_saved(scene,state,view,config,options.model_dump(mode='json'),cancelled,project_root=root)
        results.append(result)
        cache=safe_path(root,f"renders/{scene.revision_id}/{state.state_id}/{result['input_hash']}")
        if not cache.exists():shutil.copytree(view/'output',cache)
        artifact=dict(result['artifacts'][0]);artifact['path']=camera.camera_id+'.png'
        shutil.copyfile(view/'output'/result['artifacts'][0]['path'],out/artifact['path']);artifacts.append(artifact)
    if len({r['geometry_hash_after'] for r in results})!=1 or any(r['root_poses']!=results[0]['root_poses'] or r['presence']!=results[0]['presence'] for r in results):
        raise DomainError('OUTPUT_INVALID','視点間で保存3Dの構造・配置が異なります。')
    source=safe_path(root,scene.saved_geometry.relative_path)
    shutil.copyfile(source,out/'lightweight.blend')
    result={'input_hash':content_hash([r['input_hash'] for r in results]),'revision_id':scene.revision_id,
        'state_id':state.state_id,'artifacts':artifacts,'object_ids':results[0]['object_ids'],
        'geometry_hash':results[0]['geometry_hash_after'],'root_poses':results[0]['root_poses'],
        'presence':results[0]['presence'],'input_unchanged':True,'source_unchanged':all(r['source_unchanged'] for r in results),
        'backend':'existing_blender_mcp','status':'succeeded','room_systems_validation':results[0].get('room_systems_validation'),
        'source_blend_sha256':scene.saved_geometry.sha256,'view_results':[r['input_hash'] for r in results]}
    atomic_json(out/'result.json',result)
    return result

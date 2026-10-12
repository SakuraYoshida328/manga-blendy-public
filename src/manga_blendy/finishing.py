"""Optional plan-only edit handoff and conservative candidate review.

No network client, API key, automatic model switch or master mutation here.
Raster checks screen for obvious drift; they cannot prove object counts or style.
"""
from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path
from typing import Literal

import numpy as np
from filelock import FileLock
from PIL import Image, ImageStat,ImageOps
from pydantic import Field
from scipy.ndimage import gaussian_filter, sobel, distance_transform_edt

from .domain import DomainError, Model, content_hash, now, uid
from .storage import MAX_UPLOAD, atomic_json, file_hash, read_json, safe_path

CHECKS = {
    'camera': '構図・パース・画面範囲が同じ',
    'architecture': '窓・ドア・壁・天井の位置と分割が同じ',
    'furniture': '家具の位置・寸法・向きが同じ',
    'contacts': '輪郭・接触・遮蔽が維持されている',
    'counts': '家具・小物の数が増減していない',
    'retired_props': '外した服や装飾が復活していない',
}

class FinishReview(Model):
    candidate_id: str = Field(pattern=r'^finish_[a-f0-9]{16}$')
    structure: dict[str, bool]
    appearance_approved: bool
    notes: str = Field(default='', max_length=2000)


def edit_prompt(request):
    return (
        'Edit stage1.png; it is the ONLY geometry/camera/framing authority. '
        'style_reference.png is for linework, material and tone ONLY, never its layout. '
        'Restore a Japanese monochrome manga background within existing surfaces: '
        'soft cloth folds, subtle wood grain, luminous walls, fine ink and screen tones. '
        'Preserve all silhouettes, contacts, occlusions, window/door partitions, '
        'furniture/prop counts, positions and perspective. Do not recover removed items '
        'from the style reference; do not add furnishings or decorations. '
        f"Respect this line/detail profile: {request['profile']}. "
        'Output one PNG at the exact stage1 resolution. If this cannot be maintained, '
        'return a candidate for review, never replace the geometry or formal master.'
    )


def prepare_handoff(job_dir: Path, result: dict, scene, state, style_path: Path):
    """Snapshots all edit inputs, retains the direct Blender result and its cache."""
    out = job_dir / 'output'
    folder = out / 'finish'
    folder.mkdir(exist_ok=True)
    path = folder / 'request.json'
    if path.exists():
        request = read_json(path)
        if request['base_input_hash'] != result['input_hash'] or request.get('style_input_sha256',request['style_sha256']) != file_hash(style_path):
            raise DomainError('FINISH_INPUT_CHANGED', '既存の仕上げ入力が異なります。新しい出力要求を作ってください。')
        verify_handoff(folder)
        return request
    artifact = result['artifacts'][0]
    base = safe_path(out, artifact['path'])
    if file_hash(base) != artifact['sha256'] or not result.get('source_unchanged'):
        raise DomainError('OUTPUT_INVALID', '構造基準の出力が変化しています。')
    shutil.copyfile(base, folder / 'stage1.png')
    with Image.open(style_path) as im:
        if im.format=='PNG' and im.getexif().get(274,1)==1:shutil.copyfile(style_path,folder/'style_reference.png')
        else:ImageOps.exif_transpose(im).convert('RGB').save(folder/'style_reference.png')
    request = {
        'schema_version':1, 'workflow':'two_stage', 'status':'awaiting_external_edit',
        'provider_route':'plan_only_external_handoff', 'api_key_allowed':False,
        'paid_fallback_allowed':False, 'automatic_generation':False,
        'model_id':None, 'created_at':now(), 'revision_id':scene.revision_id,
        'state_id':state.state_id, 'camera_id':result['camera_id'],
        'camera':next(c.model_dump(mode='json') for c in scene.cameras if c.camera_id==result['camera_id']),
        'scene_hash':content_hash(scene),
        'state_hash':content_hash(state),
        'base_input_hash':result['input_hash'], 'base_sha256':file_hash(folder/'stage1.png'),
        'base_result_sha256':file_hash(out/'result.json'),
        'base_artifact_path':artifact['path'],
        'geometry_sha256':scene.saved_geometry.sha256,
        'geometry_hash':result['geometry_hash_after'],
        'style_sha256':file_hash(folder/'style_reference.png'),
        'style_input_sha256':file_hash(style_path),
        'profile':result['profile'], 'geometry_authority':'stage1.png',
        'appearance_reference':'style_reference.png',
        'presence':{key:item.presence for key,item in state.objects.items()},
        'object_ids':sorted(item.object_id for item in scene.objects),
        'window_openings':result.get('window_openings',{}),
        'review_checks':CHECKS, 'formal_workflow_changed':False,
    }
    request['prompt'] = edit_prompt(request)
    request['material_components']=[{'object_id':o.object_id,'material_class':o.material_class,
        'components':[p.model_dump(mode='json') for p in o.components],'reference_ids':o.reference_ids}
        for o in scene.objects if state.objects[o.object_id].presence=='placed']
    atomic_json(path,request)
    (folder/'edit_prompt.txt').write_text(request['prompt'],encoding='utf-8')
    return request


def export_handoff(folder: Path):
    """Self-contained inputs for a new external editing session, with no chat memory."""
    request=verify_handoff(folder)
    target=folder/'handoff.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in ['stage1.png','style_reference.png','request.json','edit_prompt.txt']:
            archive.write(folder/name,name)
        archive.writestr('README.txt','Image 1 stage1.png = geometry, camera, silhouette, occlusion.\n'
            'Image 2 style_reference.png = material/ink/tone only, NEVER its layout.\n'
            'Follow edit_prompt.txt and request.json. Return one same-size grayscale PNG.\n'
            'Use only an available plan-authorized editor or local processing; no paid API fallback.\n'
            'This application cannot automatically invoke the Codex image-generation tool.\n'
            'The edited result requires structural and appearance review before use.\n')
    return target


def verify_handoff(folder):
    request = read_json(folder/'request.json')
    checks = [(folder/'stage1.png',request['base_sha256']),
              (folder/'style_reference.png',request['style_sha256']),
              (folder.parent/'result.json',request['base_result_sha256']),
              (safe_path(folder.parent,request['base_artifact_path']),request['base_sha256'])]
    if any(not path.is_file() or file_hash(path)!=expected for path,expected in checks):
        raise DomainError('FINISH_INPUT_CHANGED','仕上げの基準画像・結果が変化しています。新しい試験を作ってください。')
    return request


def _edges(image):
    # Downsampling and smoothing limit false matches from newly drawn fine textures.
    im = image.copy()
    im.thumbnail((512,512),Image.Resampling.LANCZOS)
    gray = gaussian_filter(np.asarray(im.convert('L'),dtype=float),1.0)
    strength = np.hypot(sobel(gray,axis=0),sobel(gray,axis=1))
    threshold = max(28.0,float(np.quantile(strength,.88)))
    return strength >= threshold


def screen_structure(base: Path, candidate: Path):
    """Heuristic image screen, explicitly NOT a geometry/semantic guarantee."""
    with Image.open(base) as b, Image.open(candidate) as c:
        reasons=[]
        if b.size != c.size:reasons.append('resolution_changed')
        if c.format != 'PNG':reasons.append('not_png')
        rgb=np.asarray(c.convert('RGB'),dtype=np.int16)
        if np.max(rgb.max(axis=2)-rgb.min(axis=2))>2:reasons.append('not_monochrome')
        if max(ImageStat.Stat(c.convert('L')).stddev)<2:reasons.append('blank_image')
        if reasons:return {'passed':False,'reasons':reasons,'semantic_guarantee':False}
        be,ce=_edges(b),_edges(c)
    if be.sum()<40 or ce.sum()<40:
        return {'passed':False,'reasons':['insufficient_edges'],'semantic_guarantee':False}
    distance=distance_transform_edt(~ce)
    match=distance<=2.0
    coverage=float(match[be].mean())
    regions=[]
    h,w=be.shape
    for y0,y1 in [(0,h//2),(h//2,h)]:
        for x0,x1 in [(0,w//2),(w//2,w)]:
            mask=be[y0:y1,x0:x1]
            if mask.sum()>=20:regions.append(float(match[y0:y1,x0:x1][mask].mean()))
    passed=coverage>=.70 and all(v>=.50 for v in regions)
    return {'passed':passed,'reasons':[] if passed else ['structural_edge_drift'],
            'edge_coverage':round(coverage,4),'quadrant_coverage':[round(v,4) for v in regions],
            'tolerance_at_512px':2.0,'semantic_guarantee':False,
            'limitation':'画像輪郭の簡易検査です。物体数・微小な移動・布の輪郭は目視確認が必要です。'}


def import_candidate(folder: Path, data: bytes,
                     provider: Literal['codex_builtin','chatgpt_manual','local_edit'],
                     model_label: str = 'unknown'):
    if provider not in {'codex_builtin','chatgpt_manual','local_edit'}:
        raise DomainError('INPUT_INVALID','利用枠内の手動編集またはローカル処理だけを選べます。')
    import re
    if not re.fullmatch(r'[A-Za-z0-9_. -]{1,80}',model_label):
        raise DomainError('INPUT_INVALID','モデル名は80文字以下の表示名にしてください。秘密情報は入力しないでください。')
    if len(data)>MAX_UPLOAD:raise DomainError('IMAGE_INVALID','画像が大きすぎます。')
    verify_handoff(folder)
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format!='PNG' or im.width>4096 or im.height>4096 or min(im.size)<64 or 'A' in im.getbands():
                raise DomainError('IMAGE_INVALID','64〜4096pxの不透明PNGが必要です。')
            im.verify()
    except (OSError,ValueError) as exc:
        if isinstance(exc,DomainError):raise
        raise DomainError('IMAGE_INVALID','PNGを読み込めません。') from exc
    cid=uid('finish');image=folder/f'{cid}.png'
    raw=folder/f'{cid}.source.png';raw.write_bytes(data)
    # Export normalization only. Keep the supplied candidate intact for provenance.
    with Image.open(io.BytesIO(data)) as im:im.convert('L').save(image)
    screen=screen_structure(folder/'stage1.png',image)
    result={'candidate_id':cid,'image':image.name,'sha256':file_hash(image),'provider':provider,
            'model_label':model_label,'created_at':now(),'screen':screen,'source_image':raw.name,
            'source_sha256':file_hash(raw),'normalization':'PNG grayscale export; no geometry edits',
            'status':'awaiting_user_review' if screen['passed'] else 'rejected',
            'review':None,'formal_workflow_changed':False,'paid_api_calls':0}
    atomic_json(folder/f'{cid}.json',result)
    return result


def approved_candidate(folder):
    verify_handoff(folder)
    candidates=[]
    for path in folder.glob('finish_*.json'):
        item=read_json(path)
        if item.get('status')=='approved_output':
            if file_hash(safe_path(folder,item['image']))!=item['sha256']:
                raise DomainError('OUTPUT_INVALID','承認された画像が変化しています。')
            candidates.append(item)
    return max(candidates,key=lambda c:c['review']['reviewed_at']) if candidates else None


def publish_approved_output(project_root: Path,folder: Path):
    candidate=approved_candidate(folder)
    if candidate is None:raise DomainError('FINISH_REVIEW_REQUIRED','承認された仕上げ画像がありません。')
    request=verify_handoff(folder)
    destination=safe_path(project_root,f"renders/{request['revision_id']}/{request['state_id']}/{request['base_input_hash']}")
    destination.mkdir(parents=True,exist_ok=True)
    base=folder.parent
    if not (destination/'result.json').exists():
        shutil.copyfile(base/'result.json',destination/'result.json')
        shutil.copyfile(safe_path(base,request['base_artifact_path']),safe_path(destination,request['base_artifact_path']))
    if read_json(destination/'result.json')['input_hash']!=request['base_input_hash']:
        raise DomainError('OUTPUT_INVALID','保存出力の基準が一致しません。')
    image=safe_path(destination,f"finished/{candidate['candidate_id']}.png");image.parent.mkdir(exist_ok=True)
    shutil.copyfile(safe_path(folder,candidate['image']),image)
    artifact={'camera_id':request['camera_id'],'path':image.relative_to(destination).as_posix(),
        'sha256':candidate['sha256'],'resolution':request['profile']['resolution'],'status':'user_approved'}
    # Keep the base result.json immutable, so old handoffs remain verifiable.
    atomic_json(destination/'approved_finish.json',{'base_input_hash':request['base_input_hash'],
        'candidate_id':candidate['candidate_id'],'artifact':artifact,'review':candidate['review'],
        'provider':candidate['provider'],'model_label':candidate['model_label']})
    return artifact


def review_candidate(folder: Path, review: FinishReview):
    if set(review.structure)!=set(CHECKS):
        raise DomainError('FINISH_REVIEW_REQUIRED','構造の全項目を確認してください。')
    with FileLock(str(folder/'review.lock')):
        verify_handoff(folder)
        path=safe_path(folder,review.candidate_id+'.json')
        result=read_json(path)
        image=safe_path(folder,result['image'])
        if file_hash(image)!=result['sha256']:raise DomainError('OUTPUT_INVALID','候補PNGが変化しています。')
        result['screen']=screen_structure(folder/'stage1.png',image)
        accepted=result['screen']['passed'] and all(review.structure.values()) and review.appearance_approved
        result['status']='approved_output' if accepted else 'rejected'
        result['review']={**review.model_dump(mode='json'),'reviewed_at':now(),'actor':'user'}
        atomic_json(path,result)
    # Approval concerns this output only. Never write SceneRevision/SceneState/result.json.
    return result

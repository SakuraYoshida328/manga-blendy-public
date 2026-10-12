"""Create a clearly labelled development packet from freshly rendered procedural fixtures.
No AI observation is claimed. No existing user 3D model or asset is loaded.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from fixtures import room_fixture
from manga_blendy.blender_adapter import render
from manga_blendy.config import APP_ROOT,load_config
from manga_blendy.domain import ReferenceEntry,uid,new_state
from manga_blendy.jobs import JobManager
from manga_blendy.storage import Store,atomic_json,image_asset
from PIL import Image


def main():
    temp=APP_ROOT/'.work'/uid('demo')
    temp.mkdir(parents=True)
    scene,state=room_fixture(resolution=(640,480))
    config=load_config();config['render_samples']=8
    result=render(scene,state,temp,config)
    store=Store(APP_ROOT/'projects')
    manifest=store.create('開発用：一室の3D連携試験（AI解析なし）',(temp/'output/source.png').read_bytes(),
                          {'place':'部屋','usage':'開発用試験','policy':'preserve','fixed':'architecture','temporal':'same_scene'})
    scene.source_asset_id=manifest.assets[0].asset_id
    scene.master_image.asset_id=scene.master_image.source_asset_id=manifest.assets[0].asset_id
    # Hand labelled visible regions belong to this development fixture only.
    boxes={'window':(.44,.25,.16,.27),'desk':(.41,.5,.23,.22),'bed':(0,.58,.4,.36),'shelf':(.58,.33,.09,.25),'bag':(.75,.64,.08,.13)}
    assets=[]
    root=store.project(manifest.project_id)
    with Image.open(temp/'output/source.png') as im:
        for oid,(x,y,w,h) in boxes.items():
            relative=f'assets/crops/demo/{oid}.png'
            (root/relative).parent.mkdir(parents=True,exist_ok=True)
            im.crop((int(x*im.width),int(y*im.height),int((x+w)*im.width),int((y+h)*im.height))).save(root/relative)
            asset=image_asset(root,relative,'crop',manifest.assets[0].asset_id);assets.append(asset)
            ref=ReferenceEntry(reference_id=uid('ref'),owner_object_id=oid,source_asset_id=manifest.assets[0].asset_id,
                               normalized_asset_id=manifest.assets[1].asset_id,bbox_xywh=(x,y,w,h),crop_asset_id=asset.asset_id,
                               visible_surface='開発試験の手指定範囲',role='shape',description='新規の手定義3Dからレンダリングした試験参照。実AI解析なし。')
            scene.references.append(ref)
            next(o for o in scene.objects if o.object_id==oid).reference_ids.append(ref.reference_id)
    manager=JobManager(store)
    jid=manager.enqueue(manifest.project_id,'fixture',scene,[state])
    atomic_json(root/f'jobs/{jid}/assets.json',[a.model_dump(mode='json') for a in assets])
    manager.start()
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        job=manager.job(manifest.project_id,jid)
        if job['status'] in ('succeeded','failed','interrupted','cancelled'):
            break
        time.sleep(.5)
    manager.close()
    if job['status']!='succeeded':
        raise RuntimeError(job)
    store.adopt(manifest.project_id,jid,None)
    later=new_state(scene,state,'翌日（全物体の配置を継承）',[])
    store.save_state(manifest.project_id,later,state.state_id)
    # Reopen prior state and assert continuity, then restore original as active with a new snapshot.
    assert store.state(manifest.project_id,state.state_id).objects==store.state(manifest.project_id,later.state_id).objects
    restored=new_state(scene,later,'確認用：元配置を再開',[])
    store.save_state(manifest.project_id,restored,later.state_id)
    export=store.export(manifest.project_id)
    imported=Store(APP_ROOT/'.work/import-test')
    imported_id=imported.import_zip(export.read_bytes())
    assert imported.state(imported_id,state.state_id).objects==state.objects
    # The adopted original state keeps the verified render available at start.
    active=store.manifest(manifest.project_id)
    active.active_state_id=state.state_id
    atomic_json(root/'project.json',active)
    summary={'project_id':manifest.project_id,'job_id':jid,'status':job['status'],'elapsed_seconds':job['elapsed_seconds'],
             'real_ai':False,'blender':result['blender_version'],'export':str(export),'reopen_and_inherit':True}
    atomic_json(APP_ROOT/'.work/demo-result.json',summary)
    print(__import__('json').dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

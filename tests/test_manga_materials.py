import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pydantic import ValidationError

from fixtures import room_fixture
from test_domain_storage import prepare
from manga_blendy.domain import (DomainError, MaterialClass, ObjectComponent, SavedGeometry,
    SceneRevision, content_hash, new_state)
from manga_blendy.material_catalog import classify_materials
from manga_blendy.manga_style import MangaRenderOptions, BackgroundRenderRequest
from manga_blendy.mcp_render import render_saved
from manga_blendy.storage import atomic_json, file_hash, read_json
from manga_blendy.web.app import create_app


def test_material_upgrade_keeps_identity_geometry_and_time_state():
    scene, state = room_fixture()
    before = {o.object_id:(o.category,o.management_role,o.dimensions_m,o.opening,o.field_locks) for o in scene.objects}
    state_hash = content_hash(state)
    classify_materials(scene)
    assert before == {o.object_id:(o.category,o.management_role,o.dimensions_m,o.opening,o.field_locks) for o in scene.objects}
    assert content_hash(state) == state_hash
    assert {c.material_class for o in scene.objects for c in o.components} == set(MaterialClass)
    bed = next(o for o in scene.objects if o.object_id == 'bed')
    assert {c.component_id:c.material_class for c in bed.components}['duvet'] == MaterialClass.FABRIC_FURNITURE
    assert next(o for o in scene.objects if o.object_id == 'chair').components[1].material_class == MaterialClass.STANDARD_FURNITURE
    assert new_state(scene,state,'翌日',[]).objects == state.objects


@pytest.mark.parametrize('version',[1,2])
def test_legacy_data_needs_no_rewrite(version):
    scene,_ = room_fixture(); data = scene.model_dump(mode='json'); data['schema_version'] = version
    data.pop('saved_geometry')
    for o in data['objects']:
        o.pop('material_class'); o.pop('components')
    text = json.dumps(data)
    loaded = SceneRevision.model_validate_json(text)
    assert loaded.saved_geometry is None and all(not o.components for o in loaded.objects)
    assert text == json.dumps(data)


def test_component_cannot_reference_another_objects_crop():
    scene,_ = room_fixture(); obj=scene.objects[0]
    obj.components=[ObjectComponent(component_id='body',label='床',material_class='building',reference_ids=['foreign'])]
    with pytest.raises(ValidationError):SceneRevision.model_validate(scene.model_dump(mode='json'))


def test_independent_line_and_detail_controls_and_stage_gate():
    low=MangaRenderOptions(focus_mode='character',character_outline_px=2,detail_level='high').resolved()
    high=MangaRenderOptions(focus_mode='background',character_outline_px=2,detail_level='high').resolved()
    assert low['main_line_px']<2 and high['main_line_px']==2
    assert low['detail_strength']==high['detail_strength']
    assert MangaRenderOptions(focus_mode='character',detail_level='low').resolved()['main_line_px']==MangaRenderOptions(focus_mode='character',detail_level='high').resolved()['main_line_px']
    with pytest.raises(ValidationError):BackgroundRenderRequest(stage='storyboard',camera_id='source')
    with pytest.raises(ValidationError):MangaRenderOptions(resolution=(0,1086))
    with pytest.raises(ValidationError):SavedGeometry(relative_path='../foreign.blend',sha256='a'*64)


def test_saved_renderer_cache_profile_and_integrity(tmp_path,monkeypatch):
    scene,state=room_fixture(); root=tmp_path/'project'; job=root/'jobs/job_test'; job.mkdir(parents=True)
    source=root/'revisions/test/lightweight.blend';source.parent.mkdir(parents=True);source.write_bytes(b'fixture, never executed')
    scene.saved_geometry=SavedGeometry(relative_path=source.relative_to(root).as_posix(),sha256=file_hash(source))
    calls=[]
    def fake_mcp(path,timeout):
        calls.append(1); request=read_json(job/'manga_render_request.json');out=job/'output'
        image=Image.new('L',tuple(request['profile']['resolution']),255);ImageDraw.Draw(image).rectangle((10,10,70,70),fill=20)
        png=out/'source.png';image.save(png)
        atomic_json(out/'result.json',{'input_hash':request['input_hash'],'revision_id':scene.revision_id,'state_id':state.state_id,
            'camera_id':'source','profile':request['profile'],'object_ids':sorted(state.objects),'geometry_hash_before':'same','geometry_hash_after':'same',
            'source_unchanged':True,'presence':{oid:s.presence for oid,s in state.objects.items()},
            'root_poses':{oid:{'position':__import__('manga_blendy.domain',fromlist=['world_pose']).world_pose(scene,state,oid)[0].tolist(),
                'quaternion':__import__('manga_blendy.domain',fromlist=['world_pose']).world_pose(scene,state,oid)[1].as_quat(scalar_first=True).tolist()} for oid,s in state.objects.items() if s.presence=='placed'},
            'artifacts':[{'path':'source.png','sha256':file_hash(png)}]})
    monkeypatch.setattr('manga_blendy.mcp_render.execute_file',fake_mcp)
    config={'render_samples':8,'blender_timeout':600};options={'camera_id':'source','render':{'resolution':[384,288]}}
    first=render_saved(scene,state,job,config,options)
    assert render_saved(scene,state,job,config,options)['input_hash']==first['input_hash'] and len(calls)==1
    import shutil
    cache=root/f'renders/{scene.revision_id}/{state.state_id}/{first["input_hash"]}'
    shutil.copytree(job/'output',cache)
    other=root/'jobs/job_other';other.mkdir()
    assert render_saved(scene,state,other,config,options)['input_hash']==first['input_hash'] and len(calls)==1
    options['render']['detail_level']='high'
    assert render_saved(scene,state,job,config,options)['input_hash']!=first['input_hash'] and len(calls)==2
    source.write_bytes(b'changed')
    with pytest.raises(DomainError,match='ハッシュ'):render_saved(scene,state,job,config,options)


def test_single_view_route_requires_prepared_geometry_and_no_storyboard(tmp_path):
    store,manifest,scene,state=prepare(tmp_path);app=create_app(tmp_path,start_workers=False)
    with TestClient(app) as client:
        text=client.get(f'/projects/{manifest.project_id}').text
        assert '素材別の描画処理' in text and '人物主役' in text
        token=re.search('name="manga-token" content="([^"]+)"',text).group(1)
        endpoint=f'/api/projects/{manifest.project_id}/states/{state.state_id}/render-image';headers={'x-manga-token':token}
        response=client.post(endpoint,json={'camera_id':'source'},headers=headers)
        assert response.json()['error']['code']=='SAVED_GEOMETRY_REQUIRED'
        scene.saved_geometry=SavedGeometry(relative_path=f'revisions/{scene.revision_id}/lightweight.blend',sha256='a'*64)
        atomic_json(store.project(manifest.project_id)/f'revisions/{scene.revision_id}/scene.json',scene)
        assert client.post(endpoint,json={'stage':'storyboard','camera_id':'source'},headers=headers).status_code==400
        response=client.post(endpoint,json={'camera_id':'source','render':{'focus_mode':'balanced','detail_level':'high'}},headers=headers)
        assert response.status_code==200
        job=app.state.manager.job(manifest.project_id,response.json()['job_id'])
        assert job['kind']=='render_image' and job['api_attempts']==0
        assert read_json(store.project(manifest.project_id)/f"jobs/{job['job_id']}/render_options.json")['render']['detail_level']=='high'


def test_retired_clothing_cannot_reappear_as_day_changes():
    scene,state=room_fixture();bag=next(o for o in scene.objects if o.object_id=='bag');bag.field_locks.append('placement')
    from manga_blendy.domain import ObjectState
    state.objects['bag']=ObjectState(presence='absent')
    assert new_state(scene,state,'翌日',[]).objects['bag'].presence=='absent'
    with pytest.raises(DomainError,match='placement'):
        new_state(scene,state,'翌日',[{'object_id':'bag','op':'SetPresence','value':{'presence':'placed','pose':{'translation_m':[1,1,0]}}}])


def test_saved_geometry_survives_adoption_export_import(tmp_path):
    from manga_blendy.storage import Store
    from test_domain_storage import source_bytes
    store=Store(tmp_path);manifest=store.create('保存3D試験',source_bytes(),{})
    scene,state=room_fixture(manifest.assets[0].asset_id);root=store.project(manifest.project_id);job=root/'jobs/job_fixture';(job/'output').mkdir(parents=True)
    source=job/'output/lightweight.blend';source.write_bytes(b'hashed saved scene fixture')
    scene.saved_geometry=SavedGeometry(relative_path=source.relative_to(root).as_posix(),sha256=file_hash(source))
    atomic_json(job/'job.json',{'status':'succeeded'});atomic_json(job/'candidate_scene.json',scene)
    atomic_json(job/'candidate_states.json',[state.model_dump(mode='json')]);atomic_json(job/'assets.json',[]);atomic_json(job/'validation.json',{})
    store.adopt(manifest.project_id,job.name,None)
    adopted=store.revision(manifest.project_id,scene.revision_id)
    assert adopted.saved_geometry.relative_path==f'revisions/{scene.revision_id}/lightweight.blend'
    imported=store.import_zip(store.export(manifest.project_id).read_bytes())
    copied=store.revision(imported,scene.revision_id)
    assert file_hash(store.project(imported)/copied.saved_geometry.relative_path)==scene.saved_geometry.sha256


def test_part_material_correction_keeps_mixed_furniture_and_requires_reprepare():
    from manga_blendy.revisions import revise
    scene,state=room_fixture();classify_materials(scene)
    scene.saved_geometry=SavedGeometry(relative_path='revisions/test/scene.blend',sha256='a'*64)
    updated,states=revise(scene,[state],[{'object_id':'bed','component_materials':{'headboard':'fabric_furniture'}}])
    parts={c.component_id:c.material_class for c in next(o for o in updated.objects if o.object_id=='bed').components}
    assert parts['headboard']=='fabric_furniture' and parts['frame']=='standard_furniture'
    assert updated.saved_geometry is None and states[0].objects==state.objects


def test_ambient_book_groups_keep_count_evidence_identity_and_promotion():
    from manga_blendy.detail_groups import group_ambient_books
    from manga_blendy.domain import SceneObject,ObjectState,Pose
    from manga_blendy.revisions import revise
    scene,state=room_fixture()
    for i in range(4):
        oid=f'shelf_book_{i}';scene.objects.append(SceneObject(object_id=oid,label=f'本棚の本 {i}',category='prop',importance='ambient',template_id='book',dimensions_m=(.04,.15,.22)))
        state.objects[oid]=ObjectState(presence='placed',pose=Pose(translation_m=(i*.05,0,.5),anchor_ref='shelf'))
    before=content_hash(state);ids=[o.object_id for o in scene.objects];group_ambient_books(scene,state)
    assert content_hash(state)==before and [o.object_id for o in scene.objects]==ids
    assert scene.detail_groups[0].estimated_count==4 and scene.detail_groups[0].count_evidence.basis=='estimated'
    assert new_state(scene,state,'翌日',[]).objects==state.objects
    with pytest.raises(DomainError,match='重要物'):
        new_state(scene,state,'x',[{'object_id':'shelf_book_0','op':'SetPresence','value':{'presence':'absent'}}])
    promoted,states=revise(scene,[state],[{'object_id':'shelf_book_0','importance':'narrative'}])
    assert 'shelf_book_0' not in promoted.detail_groups[0].member_object_ids
    assert promoted.detail_groups[0].estimated_count==3
    assert next(o for o in promoted.objects if o.object_id=='shelf_book_0').geometry_detail=='standard'


def test_atomic_json_retries_transient_windows_lock_without_partial_write(tmp_path,monkeypatch):
    import manga_blendy.storage as storage
    target=tmp_path/'data.json';target.write_text('{"old":true}')
    original=storage.os.replace;calls=[]
    def replace(source,destination):
        calls.append(1)
        if len(calls)==1:
            error=PermissionError('transient file scanner lock');error.winerror=32;raise error
        return original(source,destination)
    monkeypatch.setattr(storage.os,'replace',replace)
    storage.atomic_json(target,{'new':True})
    assert read_json(target)=={'new':True} and len(calls)==2 and not list(tmp_path.glob('*.tmp'))

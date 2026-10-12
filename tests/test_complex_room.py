import hashlib
import json
import os
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pydantic import ValidationError

from fixtures import room_fixture
from manga_blendy.analysis import DetailPatch, OpenAIProvider
from manga_blendy.appearance import prepare_surfaces
from manga_blendy.blender_adapter import render
from manga_blendy.config import load_config
from manga_blendy.domain import (Category, DomainError, ManagementPolicy, ObjectState, Pose,
    ReferenceEntry, SceneObject, SceneRevision, SurfaceMapping, content_hash, new_state)
from manga_blendy.jobs import run_job
from manga_blendy.observation_import import ObservationPackage, import_observation
from manga_blendy.organization import classify_candidate, organize_scene
from manga_blendy.storage import Store, atomic_json, image_asset, read_json
from manga_blendy.web.app import create_app
from test_domain_storage import source_bytes
from test_planning_jobs import manifest_image, proposal_for_fixture
from manga_blendy.planning import make_plan


def test_fixed_plants_and_rugs_are_furniture_not_simplification_targets():
    proposal = proposal_for_fixture()
    candidate = proposal.candidates[-1]
    for label, template in [('観葉植物の鉢', 'plant'), ('毛足のあるカーペット', 'rug')]:
        item = candidate.model_copy(deep=True)
        item.label, item.template_id = label, 'box'
        item.category, item.management_role, item.is_group = Category.PROP, 'auto', True
        assert classify_candidate(item) == 'fixed_furniture'
        assert item.category == Category.FURNITURE and item.template_id == template
        assert item.is_group is False
        item.management_role='movable_prop'
        assert classify_candidate(item)=='fixed_furniture'


def test_simplification_preserves_structure_ids_states_and_narrative_props():
    scene, state = room_fixture()
    for n in range(18):
        oid = f'phone_{n}'
        scene.objects.append(SceneObject(object_id=oid, label=f'スマホ{n}', category=Category.PROP,
            importance='narrative' if n == 0 else 'ambient', template_id='phone', dimensions_m=(.07,.14,.01)))
        state.objects[oid] = ObjectState(presence='placed', pose=Pose(translation_m=(2,1,.5)))
    scene.management_policy = ManagementPolicy(max_detailed_movable=2)
    states_before = content_hash(state)
    geometry_before = {o.object_id:(o.dimensions_m,o.opening) for o in scene.objects}
    organize_scene(scene)
    assert sum(o.geometry_detail=='proxy' for o in scene.objects) >= 15
    assert next(o for o in scene.objects if o.object_id=='phone_0').geometry_detail=='standard'
    assert all(o.geometry_detail=='standard' for o in scene.objects if o.management_role in {'building','fixed_furniture'})
    assert {o.object_id:(o.dimensions_m,o.opening) for o in scene.objects} == geometry_before
    assert content_hash(state)==states_before
    assert scene.management_report['status']=='within_budget'
    assert new_state(scene,state,'翌日',[]).objects==state.objects
    assert scene.simplification=='SIMPLIFY_INTERNAL'


def test_budget_cannot_sacrifice_protected_geometry():
    scene, state = room_fixture()
    scene.management_policy = ManagementPolicy(max_geometry_units=100, max_detailed_movable=0)
    organize_scene(scene)
    assert scene.readiness=='reference_only'
    assert scene.management_report['status']=='over_budget_protected'
    assert len(scene.objects)==len(state.objects)
    assert next(o for o in scene.objects if o.object_id=='window').opening.columns_per_row==[2]
    assert all(o.geometry_detail=='standard' for o in scene.objects if o.management_role in {'building','fixed_furniture'})


def test_higher_budget_restores_prop_detail_without_losing_identity():
    scene,state=room_fixture()
    scene.management_policy=ManagementPolicy(max_detailed_movable=0)
    organize_scene(scene)
    assert next(o for o in scene.objects if o.object_id=='bag').geometry_detail=='proxy'
    ids_before=[o.object_id for o in scene.objects]
    state_before=content_hash(state)
    scene.management_policy=ManagementPolicy(max_detailed_movable=100)
    organize_scene(scene)
    assert next(o for o in scene.objects if o.object_id=='bag').geometry_detail=='standard'
    assert [o.object_id for o in scene.objects]==ids_before and content_hash(state)==state_before


def test_legacy_scene_without_new_fields_loads():
    scene, _ = room_fixture()
    data=scene.model_dump(mode='json'); data['schema_version']=1
    data.pop('management_policy'); data.pop('management_report')
    for obj in data['objects']:
        obj.pop('management_role'); obj.pop('geometry_detail')
    loaded=SceneRevision.model_validate(data)
    assert loaded.schema_version==1 and loaded.objects[0].geometry_detail=='standard'


def test_unverified_and_whole_scene_crops_cannot_be_textures():
    values=dict(bbox_xywh=(.1,.1,.1,.1),description='pattern',role='material',visible_surface='top',
                mapping=SurfaceMapping(face='top',usage='bump'))
    with pytest.raises(ValidationError): DetailPatch(**values)
    values.update(surface_only_verified=True,bbox_xywh=(0,0,1,1))
    with pytest.raises(ValidationError): DetailPatch(**values)


def test_planner_keeps_object_crop_and_adds_face_linked_details(tmp_path):
    store,manifest=manifest_image(tmp_path)
    proposal=proposal_for_fixture()
    desk=next(c for c in proposal.candidates if c.template_id=='desk')
    desk.detail_patches=[DetailPatch(bbox_xywh=(.1,.1,.05,.05),description='wood grain patch',
        role='material',visible_surface='top',surface_only_verified=True,
        mapping=SurfaceMapping(target_part='desk_top',face='top',usage='bump'))]
    scene,states,assets=make_plan(manifest,store.project(manifest.project_id),proposal,'job_details')
    owner=next(o for o in scene.objects if o.template_id=='desk')
    refs=[r for r in scene.references if r.owner_object_id==owner.object_id]
    assert len(refs)==2 and len(owner.reference_ids)==2
    assert refs[0].surface_mapping is None and refs[1].surface_mapping.target_part=='desk_top'
    assert len(assets)==len(proposal.candidates)+1
    assert scene.management_report['mapped_surface_count']==1
    assert len(scene.cameras)==4


def material_fixture(tmp_path):
    store=Store(tmp_path)
    manifest=store.create('表面画像の連動試験',source_bytes(),{})
    root=store.project(manifest.project_id)
    pattern=Image.new('L',(64,64),225); draw=ImageDraw.Draw(pattern)
    for y in range(0,64,8):
        for x in range(0,64,8):
            if (x//8+y//8)%2: draw.rectangle((x,y,x+7,y+7),fill=35)
    path=root/'assets/crops/material.png'; path.parent.mkdir(parents=True)
    pattern.save(path)
    asset=image_asset(root,'assets/crops/material.png','crop',manifest.assets[0].asset_id)
    manifest.assets.append(asset); atomic_json(root/'project.json',manifest)
    scene,state=room_fixture(manifest.assets[0].asset_id,resolution=(192,144))
    rug=SceneObject(object_id='rug',label='カーペット',category=Category.FURNITURE,importance='structural',
                    template_id='rug',dimensions_m=(2,1.6,.025),management_role='fixed_furniture')
    scene.objects.append(rug)
    state.objects['rug']=ObjectState(presence='placed',pose=Pose(translation_m=(2.5,1.8,0)))
    for oid,template,dims,pos,category in [
        ('plant','plant',(.5,.5,.95),(5.3,1.8,0),Category.FURNITURE),
        ('phone','phone',(.075,.15,.008),(.1,0,0),Category.PROP),
        ('pen','pen',(.012,.14,.012),(-.2,0,0),Category.PROP),
        ('new_book','book',(.18,.24,.035),(0,.15,0),Category.PROP),
        ('proxy_phone','phone',(.075,.15,.008),(-.4,0,0),Category.PROP)]:
        scene.objects.append(SceneObject(object_id=oid,label=oid,template_id=template,dimensions_m=dims,
            category=category,importance='ambient',geometry_detail='proxy' if oid=='proxy_phone' else 'standard'))
        state.objects[oid]=ObjectState(presence='placed',pose=Pose(translation_m=pos,
            anchor_ref='world' if category==Category.FURNITURE else 'desk',
            anchor_key='origin' if category==Category.FURNITURE else 'top'))
    for usage in ['color','bump']:
        refid='carpet_'+usage
        rug.reference_ids.append(refid)
        scene.references.append(ReferenceEntry(reference_id=refid,owner_object_id='rug',
            source_asset_id=manifest.assets[0].asset_id,normalized_asset_id=manifest.assets[1].asset_id,
            bbox_xywh=(0,0,.25,.25),crop_asset_id=asset.asset_id,visible_surface='top',role='material',
            description='synthetic checker patch, not a user room',surface_patch_verified=True,
            surface_mapping=SurfaceMapping(face='top',usage=usage,repeat_uv=(2,2))))
    scene=SceneRevision.model_validate(scene.model_dump(mode='json'))
    job=root/'jobs/job_surface'; job.mkdir(parents=True)
    return store,manifest,scene,state,job,path


def test_surface_assets_are_bound_to_owner_face_and_checksum(tmp_path):
    store,manifest,scene,state,job,path=material_fixture(tmp_path)
    result=prepare_surfaces(scene,state,job)
    assert len(result)==2 and {r['object_id'] for r in result}=={'rug'}
    assert {r['face'] for r in result}=={'top'}
    assert all(not os.path.isabs(r['path']) for r in result)
    next(o for o in scene.objects if o.object_id=='rug').variants['changed']='different material'
    state.objects['rug'].variant_id='changed'
    assert prepare_surfaces(scene,state,job)==[]
    state.objects['rug'].variant_id='default'
    path.write_bytes(b'changed')
    with pytest.raises(DomainError) as exc: prepare_surfaces(scene,state,job)
    assert exc.value.code=='OUTPUT_INVALID'


def test_invalid_surface_target_and_duplicate_mapping_rejected(tmp_path):
    _,_,scene,_,_,_=material_fixture(tmp_path)
    data=scene.model_dump(mode='json')
    data['references'][0]['surface_mapping']['target_part']='bed_mattress'
    with pytest.raises(ValidationError): SceneRevision.model_validate(data)
    data=scene.model_dump(mode='json')
    data['references'][1]['surface_mapping']['usage']='color'
    with pytest.raises(ValidationError): SceneRevision.model_validate(data)


def test_surface_links_survive_adoption_export_and_import(tmp_path):
    store,manifest,scene,state,job,_=material_fixture(tmp_path)
    atomic_json(job/'job.json',{'status':'succeeded'})
    atomic_json(job/'candidate_scene.json',scene)
    atomic_json(job/'candidate_states.json',[state.model_dump(mode='json')])
    atomic_json(job/'assets.json',[])
    atomic_json(job/'validation.json',{'test':True})
    store.adopt(manifest.project_id,job.name,None)
    archive=store.export(manifest.project_id)
    imported=store.import_zip(archive.read_bytes())
    adopted=store.revision(imported,scene.revision_id)
    assert adopted.schema_version==3
    assert adopted.references==scene.references
    assert next(o for o in adopted.objects if o.object_id=='rug').reference_ids==['carpet_color','carpet_bump']


def test_import_source_hash_gate_and_api_key_connection_disabled(tmp_path,monkeypatch):
    image=source_bytes()
    package=ObservationPackage(source_sha256=hashlib.sha256(image).hexdigest(),observation=proposal_for_fixture())
    assert import_observation(image,package.model_dump_json().encode()).scene_type=='room'
    with pytest.raises(DomainError) as exc: import_observation(image+b'wrong',package.model_dump_json().encode())
    assert exc.value.code=='SOURCE_MISMATCH'
    monkeypatch.setenv('OPENAI_API_KEY','test-only-key')
    with pytest.raises(DomainError) as exc: OpenAIProvider('unused').observe(tmp_path/'absent',{},lambda *a:None)
    assert exc.value.code=='BILLING_DISABLED'
    with pytest.raises(DomainError) as exc: OpenAIProvider('unused',transport=object()).observe(tmp_path/'absent',{},lambda *a:None)
    assert exc.value.code=='BILLING_DISABLED'


def test_offline_web_import_finishes_without_ai_or_blender(tmp_path,monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    image=source_bytes()
    package=ObservationPackage(source_sha256=hashlib.sha256(image).hexdigest(),
                               observation=proposal_for_fixture(supported=False))
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app) as client:
        response=client.get('/new')
        token=re.search('name="manga-token" content="([^"]+)"',response.text).group(1)
        result=client.post('/projects',data={'title':'解析取込試験','token':token},
            files={'image':('source.png',image,'image/png'),
                   'observation_file':('observation.json',package.model_dump_json().encode(),'application/json')},
            follow_redirects=False)
        assert result.status_code==303
        _,_,pid,_,jid=result.headers['location'].split('/')
        run_job(str(tmp_path),pid,jid)
        job=app.state.manager.job(pid,jid)
        assert job['status']=='succeeded' and job['api_attempts']==0
        review=client.get(result.headers['location']+'/review')
        assert review.status_code==200 and '分類と小物の整理' in review.text
        assert client.get('/analysis-contract').json()['contract']=='manga-blendy.observation'


@pytest.mark.skipif(os.environ.get('MANGA_BLENDY_BLENDER_TEST')!='1',reason='real Blender opt-in integration')
def test_real_blender_surface_images_packed_follow_object_and_reopen(tmp_path):
    store,manifest,scene,state,job,_=material_fixture(tmp_path)
    config=load_config(); config['render_samples']=2
    before=content_hash({'scene':scene,'state':state})
    result=render(scene,state,job,config)
    verified=read_json(job/'output/verify.json')
    assert result['surface_reference_ids']==['carpet_bump','carpet_color']
    assert verified['surface_reference_ids']==result['surface_reference_ids']
    assert verified['packed_surface_images']==2 and len(result['artifacts'])==4
    assert verified['object_reference_ids']['rug']==['carpet_color','carpet_bump']
    assert content_hash({'scene':scene,'state':state})==before
    # A new placement keeps the exact same owner/reference/material connection.
    later=state.model_copy(deep=True); later.state_id='rug_moved'
    later.objects['rug'].pose.translation_m=(3.1,1.9,0)
    moved_job=job.parent/'job_moved'; moved_job.mkdir()
    moved=render(scene,later,moved_job,config)
    assert moved['surface_reference_ids']==result['surface_reference_ids']
    assert moved['input_hash']!=result['input_hash']
    assert read_json(moved_job/'output/verify.json')['state_id']=='rug_moved'

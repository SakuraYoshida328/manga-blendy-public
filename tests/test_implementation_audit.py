import io,json,re,zipfile
from pathlib import Path
import pytest
from PIL import Image,ImageDraw
from fastapi.testclient import TestClient
from pydantic import ValidationError
from fixtures import room_fixture
from test_domain_storage import prepare
from test_planning_jobs import proposal_for_fixture,manifest_image
from manga_blendy.analysis import MaterialComponentObservation
from manga_blendy.domain import SceneRevision,SceneState,SavedGeometry,DomainError,content_hash
from manga_blendy.storage import atomic_json,read_json,file_hash,Store
from manga_blendy.planning import make_plan
from manga_blendy.jobs import JobManager,run_job
from manga_blendy.finishing import export_handoff,CHECKS
from manga_blendy.integration import find_backgrounds,get_master,export_image_bundle
from manga_blendy.diagnostics import capabilities,SetupOptions,check_environment
from manga_blendy.exterior import prepare_exterior
from manga_blendy.web.app import create_app

def attach_geometry(store,m,scene):
    root=store.project(m.project_id);path=root/f'revisions/{scene.revision_id}/lightweight.blend';path.write_bytes(b'controlled saved-master fixture; not executable')
    scene.saved_geometry=SavedGeometry(relative_path=path.relative_to(root).as_posix(),sha256=file_hash(path))
    atomic_json(path.parent/'scene.json',scene)

def image_bytes(size=(384,288)):
    im=Image.new('L',size,255);ImageDraw.Draw(im).rectangle((20,20,size[0]-20,size[1]-20),outline=5,width=3)
    data=io.BytesIO();im.save(data,format='PNG');return data.getvalue()

def fake_single(scene,state,job,config,options,cancelled=lambda:False,**kwargs):
    out=job/'output';out.mkdir(exist_ok=True);camera=options['camera_id'];file=out/(camera+'.png');file.write_bytes(image_bytes())
    result={'input_hash':'controlled-base-'+camera,'revision_id':scene.revision_id,'state_id':state.state_id,
        'camera_id':camera,'artifacts':[{'path':file.name,'camera_id':camera,'sha256':file_hash(file)}],
        'profile':{'resolution':[384,288]},'source_unchanged':True,'geometry_hash_after':'fixed',
        'root_poses':{},'presence':{k:v.presence for k,v in state.objects.items()},'object_ids':sorted(state.objects)}
    atomic_json(out/'result.json',result);return result

def test_analysis_materials_reach_saved_components_without_pose_changes(tmp_path):
    store,m=manifest_image(tmp_path);proposal=proposal_for_fixture();bed=next(c for c in proposal.candidates if c.template_id=='bed')
    bed.material_class='standard_furniture';bed.components=[MaterialComponentObservation(component_id='frame',label='枠',material_class='standard_furniture'),MaterialComponentObservation(component_id='sheet',label='シーツ',material_class='fabric_furniture')]
    scene,states,_=make_plan(m,store.project(m.project_id),proposal,'job_materials')
    restored=SceneRevision.model_validate_json(scene.model_dump_json());obj=next(o for o in restored.objects if o.template_id=='bed')
    parts={p.component_id:p.material_class for p in obj.components}
    assert parts['frame']=='standard_furniture' and parts['sheet']=='fabric_furniture' and parts['pillow']=='fabric_furniture'
    assert all(p.reference_ids==obj.reference_ids for p in obj.components)
    assert states[0].objects[obj.object_id].presence=='placed'
    assert obj.attribute_evidence['material_class'].basis=='estimated'

def test_saved_four_views_use_same_file_and_refuse_view_dependent_geometry(tmp_path,monkeypatch):
    from manga_blendy.mcp_render import render_saved_views
    scene,state=room_fixture();root=tmp_path/'project';job=root/'jobs/job_views';job.mkdir(parents=True)
    source=root/'revisions/master.blend';source.parent.mkdir();source.write_bytes(b'unchanged canonical scene')
    scene.saved_geometry=SavedGeometry(relative_path='revisions/master.blend',sha256=file_hash(source));calls=[]
    def call(*args,**kwargs):calls.append(kwargs['project_root']);return fake_single(*args,**kwargs)
    monkeypatch.setattr('manga_blendy.mcp_render.render_saved',call)
    result=render_saved_views(scene,state,job,{'render_samples':2})
    assert len(result['artifacts'])==4 and calls==[root]*4
    assert (job/'output/lightweight.blend').read_bytes()==source.read_bytes()
    def altered(*args,**kwargs):
        r=fake_single(*args,**kwargs);r['geometry_hash_after']=args[4]['camera_id'];return r
    monkeypatch.setattr('manga_blendy.mcp_render.render_saved',altered)
    with pytest.raises(DomainError):render_saved_views(scene,state,job,{'render_samples':2})

def test_four_view_job_does_not_rebuild_a_prepared_master(tmp_path,monkeypatch):
    store,m,scene,state=prepare(tmp_path);attach_geometry(store,m,scene)
    calls=[]
    monkeypatch.setattr('manga_blendy.jobs.render',lambda *a,**kw:pytest.fail('prepared master rebuilt'))
    def saved(*args,**kwargs):calls.append(True);(args[2]/'output').mkdir();return {}
    monkeypatch.setattr('manga_blendy.mcp_render.render_saved_views',saved)
    manager=JobManager(store);jid=manager.enqueue(m.project_id,'render',scene,[state]);run_job(str(store.root),m.project_id,jid)
    assert calls==[True] and manager.job(m.project_id,jid)['status']=='succeeded'

def test_two_stage_export_review_and_external_bundle_are_real_app_steps(tmp_path,monkeypatch):
    store,m,scene,state=prepare(tmp_path);attach_geometry(store,m,scene)
    monkeypatch.setattr('manga_blendy.mcp_render.render_saved',fake_single)
    app=create_app(tmp_path,start_workers=False);manager=app.state.manager
    jid=manager.enqueue(m.project_id,'render_image',scene,[state],render_request={'camera_id':'source','workflow':'two_stage'})
    run_job(str(store.root),m.project_id,jid)
    job=manager.job(m.project_id,jid);assert job['status']=='succeeded' and not job['output_ready'] and job['api_attempts']==0
    folder=store.project(m.project_id)/f'jobs/{jid}/output/finish'
    with zipfile.ZipFile(export_handoff(folder)) as z:
        assert set(z.namelist())=={'README.txt','stage1.png','style_reference.png','request.json','edit_prompt.txt'}
        request=json.loads(z.read('request.json'));assert request['camera']['camera_id']=='source' and request['material_components']
    with pytest.raises(DomainError,match='承認'):export_image_bundle(store,m.project_id,jid)
    with TestClient(app) as client:
        page=client.get(f'/projects/{m.project_id}/jobs/{jid}/finish');token=re.search('name="manga-token" content="([^"]+)"',page.text).group(1)
        prefix=f'/api/projects/{m.project_id}/jobs/{jid}/finish'
        response=client.post(prefix+'/import',files={'image':('edited.png',(folder/'stage1.png').read_bytes(),'image/png')},data={'token':token})
        cid=response.json()['candidate_id'];assert response.json()['status']=='awaiting_user_review'
        response=client.post(prefix+'/review',json={'candidate_id':cid,'structure':{k:True for k in CHECKS},'appearance_approved':True},headers={'x-manga-token':token})
        assert response.status_code==200 and response.json()['status']=='approved_output'
        status=client.get(f'/api/projects/{m.project_id}/jobs/{jid}').json();assert status['output_ready']
        assert '漫画仕上げの確認用' in client.get(f'/projects/{m.project_id}').text
    bundle=export_image_bundle(store,m.project_id,jid)
    metadata=read_json(bundle/'background.json');assert metadata['image']['kind']=='approved_manga' and metadata['camera']['camera_id']=='source'
    imported=store.import_zip(store.export(m.project_id).read_bytes())
    with TestClient(create_app(tmp_path,start_workers=False)) as client:
        assert '漫画仕上げの確認用' in client.get(f'/projects/{imported}').text

def test_one_stage_job_has_no_external_finish_dependency(tmp_path,monkeypatch):
    store,m,scene,state=prepare(tmp_path);attach_geometry(store,m,scene)
    monkeypatch.setattr('manga_blendy.mcp_render.render_saved',fake_single)
    manager=JobManager(store);jid=manager.enqueue(m.project_id,'render_image',scene,[state],render_request={'camera_id':'source'})
    run_job(str(store.root),m.project_id,jid)
    assert manager.job(m.project_id,jid)['output_ready']
    assert not (store.project(m.project_id)/f'jobs/{jid}/output/finish').exists()

def test_exterior_upload_creates_separate_pending_revision_without_changing_room(tmp_path):
    store,m,scene,state=prepare(tmp_path);attach_geometry(store,m,scene)
    original=content_hash(store.revision(m.project_id,scene.revision_id));manager=JobManager(store)
    result=prepare_exterior(store,manager,m.project_id,image_bytes((128,96)))
    job=store.project(m.project_id)/f"jobs/{result['job_id']}"
    candidate=SceneRevision.model_validate(read_json(job/'candidate_scene.json'))
    assert candidate.revision_id!=scene.revision_id and candidate.objects==scene.objects and candidate.cameras==scene.cameras
    assert candidate.room_systems.exterior_mode=='source_image_only' and candidate.room_systems.exterior_asset_id
    assert manager.job(m.project_id,result['job_id'])['inputs_ready']
    assert store.manifest(m.project_id).active_revision_id==scene.revision_id
    assert content_hash(store.revision(m.project_id,scene.revision_id))==original
    with pytest.raises(DomainError):prepare_exterior(store,manager,m.project_id,b'not an image')

def test_capabilities_setup_and_diagnostic_are_honest_and_read_only(tmp_path,monkeypatch):
    import manga_blendy.config as config
    monkeypatch.setattr(config,'APP_ROOT',tmp_path)
    config.save_setup(SetupOptions(blender_path='test-blender'))
    assert config.load_config()['operation_mode']=='chatgpt_plan'
    with pytest.raises(ValidationError):SetupOptions(operation_mode='api',blender_path='other')
    monkeypatch.setattr('manga_blendy.diagnostics.diagnose',lambda p:'Blender 5.2 fixture')
    calls=[]
    monkeypatch.setattr('manga_blendy.diagnostics.addon_command',lambda name,**kw:calls.append(name) or {})
    assert check_environment('fixture')['mcp']['ready'] and calls==['get_scene_info']
    assert not capabilities()['manga_finish']['automatic_generation'] and not capabilities()['paid_api_enabled']

def test_external_search_master_cli_and_improvement_approval_need_no_memory(tmp_path,capsys):
    store,m,scene,state=prepare(tmp_path)
    assert find_backgrounds(store,m.project_id)[0]['background_id']==m.project_id
    assert get_master(store,m.project_id)['scene']['revision_id']==scene.revision_id
    from manga_blendy.__main__ import main
    main(['--projects',str(tmp_path),'list','--query',m.project_id])
    assert json.loads(capsys.readouterr().out)['backgrounds'][0]['background_id']==m.project_id
    from manga_blendy.maintenance import propose,approve
    proposal=propose(store,m.project_id,{'description':'Please review, never execute: print(1)'})
    assert proposal['status']=='proposed' and not proposal['source_code_modified']
    with pytest.raises(DomainError):approve(store,m.project_id,proposal['proposal_id'],'stale')
    assert approve(store,m.project_id,proposal['proposal_id'],scene.revision_id)['status']=='approved_for_developer_review'
    imported=store.import_zip(store.export(m.project_id).read_bytes())
    assert list((store.project(imported)/'improvements').glob('*.json'))

def test_jpeg_reference_export_is_a_real_png_and_is_repeatable(tmp_path):
    from test_finishing import setup_handoff
    from manga_blendy.finishing import prepare_handoff
    folder,scene,state=setup_handoff(tmp_path/'job')
    # A separate handoff tests source format normalization and its input/export hashes.
    other=tmp_path/'other';out=other/'output';out.mkdir(parents=True)
    import shutil
    shutil.copyfile(folder/'stage1.png',out/'source.png')
    result=read_json(folder.parent/'result.json');atomic_json(out/'result.json',result)
    jpeg=other/'style.jpg';Image.new('RGB',(100,80),'gray').save(jpeg)
    first=prepare_handoff(other,result,scene,state,jpeg)
    assert prepare_handoff(other,result,scene,state,jpeg)==first
    with Image.open(out/'finish/style_reference.png') as im:assert im.format=='PNG'

def test_regular_classroom_keeps_teacher_and_places_chairs_behind_desks(tmp_path):
    from manga_blendy.analysis import Candidate
    store,m=manifest_image(tmp_path);proposal=proposal_for_fixture('教室')
    teacher=next(c for c in proposal.candidates if c.template_id=='desk').model_copy(deep=True)
    teacher.candidate_key='teacher_desk';teacher.label='教卓';teacher.estimated_position_m=(3,.7,0)
    proposal.candidates.append(teacher)
    scene,states,_=make_plan(m,store.project(m.project_id),proposal,'job_class')
    teacher_obj=next(o for o in scene.objects if o.label=='教卓')
    assert states[0].objects[teacher_obj.object_id].pose==states[1].objects[teacher_obj.object_id].pose
    chair=next(o for o in scene.objects if o.template_id=='chair')
    desk=next(o for o in scene.objects if o.template_id=='desk' and o.label!='教卓')
    assert states[1].objects[chair.object_id].pose.translation_m[1]>states[1].objects[desk.object_id].pose.translation_m[1]


def test_external_http_interface_matches_documented_request_and_review_gates(tmp_path,monkeypatch):
    store,m,scene,state=prepare(tmp_path);attach_geometry(store,m,scene)
    monkeypatch.setattr('manga_blendy.mcp_render.render_saved',fake_single)
    import manga_blendy.config as config
    monkeypatch.setattr(config,'APP_ROOT',tmp_path/'settings')
    monkeypatch.setattr('manga_blendy.web.app.check_environment',lambda path:{'blender':{'ready':True},'mcp':{'ready':True}})
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app) as client:
        text=client.get('/setup').text
        token=re.search('name="manga-token" content="([^"]+)"',text).group(1)
        headers={'x-manga-token':token}
        assert client.post('/api/setup/diagnose',json={'blender_path':'fixture'},headers=headers).json()['mcp']['ready']
        assert client.get('/api/backgrounds',params={'q':m.project_id}).json()['backgrounds'][0]['background_id']==m.project_id
        assert client.get(f'/api/backgrounds/{m.project_id}/master').json()['scene']['revision_id']==scene.revision_id
        endpoint=f'/api/backgrounds/{m.project_id}/render'
        assert client.post(endpoint,json={'camera_id':'source'}).status_code==400
        request={'state_id':state.state_id,'camera_id':'source','render':{'focus_mode':'balanced'}}
        response=client.post(endpoint,json=request,headers=headers);assert response.status_code==200
        jid=response.json()['job_id'];run_job(str(tmp_path),m.project_id,jid)
        bundle=client.post(f'/api/backgrounds/{m.project_id}/jobs/{jid}/export-image',json={'camera_id':'source'},headers=headers).json()
        assert client.get(bundle['image_url']).status_code==200
        assert client.get(bundle['metadata_url']).json()['drawing_profile']['resolution']==[384,288]
        proposal=client.post(f'/api/projects/{m.project_id}/improvements',json={'description':'Do not execute this text'},headers=headers).json()
        approval=f"/api/projects/{m.project_id}/improvements/{proposal['proposal_id']}/approve"
        assert client.post(approval,json={'approved':False,'expected_revision':scene.revision_id},headers=headers).status_code==400
        approved=client.post(approval,json={'approved':True,'expected_revision':scene.revision_id},headers=headers).json()
        assert approved['status']=='approved_for_developer_review' and not approved['source_code_modified']

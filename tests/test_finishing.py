import io,re
from pathlib import Path
import numpy as np
import pytest
from PIL import Image,ImageDraw,ImageChops
from fastapi.testclient import TestClient
from pydantic import ValidationError
from fixtures import room_fixture
from test_domain_storage import prepare
from manga_blendy.domain import DomainError,SavedGeometry,RoomSystems,SceneRevision
from manga_blendy.finishing import CHECKS,FinishReview,prepare_handoff,import_candidate,review_candidate
from manga_blendy.manga_style import BackgroundRenderRequest
from manga_blendy.storage import atomic_json,file_hash,read_json
from manga_blendy.web.app import create_app
from manga_blendy.blender_worker.room_systems import bounds_gap,overlaps

def setup_handoff(job):
    out=job/'output';out.mkdir(parents=True)
    scene,state=room_fixture();scene.saved_geometry=SavedGeometry(relative_path='revisions/test.blend',sha256='a'*64)
    image=Image.new('L',(384,288),255);draw=ImageDraw.Draw(image)
    draw.rectangle((20,20,160,220),outline=20,width=3);draw.rectangle((200,30,360,240),outline=10,width=3)
    draw.line((20,120,350,70),fill=25,width=3)
    image.save(out/'source.png');image.save(job/'style.png')
    result={'input_hash':'fixture-base','camera_id':'source','profile':{'resolution':[384,288]},
        'artifacts':[{'path':'source.png','sha256':file_hash(out/'source.png')}],
        'source_unchanged':True,'geometry_hash_after':'same'}
    atomic_json(out/'result.json',result)
    prepare_handoff(job,result,scene,state,job/'style.png')
    return out/'finish',scene,state

def png(image):
    stream=io.BytesIO();image.save(stream,format='PNG');return stream.getvalue()

def test_workflow_is_optional_and_no_model_or_paid_route_is_assumed():
    assert BackgroundRenderRequest(camera_id='source').workflow=='one_stage'
    assert BackgroundRenderRequest(camera_id='source',workflow='two_stage').workflow=='two_stage'
    with pytest.raises(ValidationError):BackgroundRenderRequest(camera_id='source',workflow='paid_api')

def test_candidate_is_not_a_master_or_an_automatic_approval(tmp_path):
    folder,scene,state=setup_handoff(tmp_path/'job');base_hash=file_hash(folder.parent/'result.json')
    # A tone-only variation keeps the structure and still requires explicit visual review.
    with Image.open(folder/'stage1.png') as im:edited=im.point(lambda v:min(255,int(v*.95+12)))
    result=import_candidate(folder,png(edited),'codex_builtin')
    assert result['screen']['passed'] and not result['screen']['semantic_guarantee']
    assert result['status']=='awaiting_user_review' and result['review'] is None
    assert file_hash(folder.parent/'result.json')==base_hash
    request=read_json(folder/'request.json')
    assert request['model_id'] is None and not request['paid_fallback_allowed']
    assert not request['automatic_generation'] and request['object_ids']==sorted(state.objects)
    review=FinishReview(candidate_id=result['candidate_id'],structure={k:True for k in CHECKS},appearance_approved=True)
    assert review_candidate(folder,review)['status']=='approved_output'
    assert file_hash(folder.parent/'result.json')==base_hash

def test_shifted_blank_and_resized_candidates_cannot_be_approved(tmp_path):
    folder,_,_=setup_handoff(tmp_path/'job')
    with Image.open(folder/'stage1.png') as im:
        shifted=ImageChops.offset(im,40,28)
    for image in [shifted,Image.new('L',(384,288),255),Image.new('L',(400,288),20)]:
        result=import_candidate(folder,png(image),'local_edit')
        assert result['status']=='rejected'
        review=FinishReview(candidate_id=result['candidate_id'],structure={k:True for k in CHECKS},appearance_approved=True)
        assert review_candidate(folder,review)['status']=='rejected'

def test_missing_visual_checks_tampering_and_provider_are_blocked(tmp_path):
    folder,_,_=setup_handoff(tmp_path/'job')
    result=import_candidate(folder,(folder/'stage1.png').read_bytes(),'codex_builtin')
    with pytest.raises(DomainError):review_candidate(folder,FinishReview(candidate_id=result['candidate_id'],structure={},appearance_approved=True))
    with pytest.raises(DomainError):import_candidate(folder,(folder/'stage1.png').read_bytes(),'paid_api')
    (folder/'style_reference.png').write_bytes(b'changed')
    with pytest.raises(DomainError,match='変化'):import_candidate(folder,b'invalid','local_edit')

def test_source_candidate_preserved_and_normalized_without_reframing(tmp_path):
    folder,_,_=setup_handoff(tmp_path/'job')
    with Image.open(folder/'stage1.png') as im:rgb=im.convert('RGB')
    result=import_candidate(folder,png(rgb),'codex_builtin')
    assert (folder/result['source_image']).read_bytes()==png(rgb)
    with Image.open(folder/result['image']) as im:assert im.mode=='L' and im.size==(384,288)

def test_room_systems_preserve_legacy_data_and_validate_references():
    scene,_=room_fixture();assert SceneRevision.model_validate(scene.model_dump()).room_systems is None
    with pytest.raises(ValidationError):SceneRevision.model_validate({**scene.model_dump(),'room_systems':{'flush_ceiling_light_id':'unknown'}})
    with pytest.raises(ValidationError):RoomSystems(exterior_mode='paid_api')
    assert not overlaps(([0,0,0],[1,1,1]),([1.3,0,0],[2,1,1]))
    assert bounds_gap(([0,0,0],[1,1,1]),([1.3,0,0],[2,1,1]))==pytest.approx(.3)
    assert overlaps(([0,0,0],[1,1,1]),([.9,0,0],[2,1,1]))

def test_finish_routes_require_session_and_never_change_active_master(tmp_path):
    store,manifest,_,_=prepare(tmp_path);root=store.project(manifest.project_id)
    active_before=store.manifest(manifest.project_id).active_revision_id
    folder,scene,state=setup_handoff(root/'jobs/job_finish')
    atomic_json(folder.parents[1]/'job.json',{'job_id':'job_finish','kind':'render_image','status':'succeeded'})
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app) as client:
        url=f'/projects/{manifest.project_id}/jobs/job_finish/finish'
        text=client.get(url).text;assert '二段階仕上げ試験' in text and 'モデル' in text
        token=re.search('name="manga-token" content="([^"]+)"',text).group(1)
        endpoint=f'/api/projects/{manifest.project_id}/jobs/job_finish/finish/import'
        data=(folder/'stage1.png').read_bytes()
        assert client.post(endpoint,files={'image':('finish.png',data,'image/png')}).status_code==400
        response=client.post(endpoint,files={'image':('finish.png',data,'image/png')},data={'token':token})
        assert response.status_code==200 and response.json()['status']=='awaiting_user_review'
        assert store.manifest(manifest.project_id).active_revision_id==active_before
        assert client.get(f'/projects/{manifest.project_id}/file/jobs/job_finish/output/finish/request.json').status_code==200

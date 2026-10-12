import io

import numpy as np
import pytest
from PIL import Image

from fixtures import room_fixture
from manga_blendy.analysis import Candidate, Landmark, ObservationProposal
from manga_blendy.domain import content_hash, new_state
from manga_blendy.jobs import JobManager, run_job
from manga_blendy.planning import make_plan, project
from manga_blendy.storage import Store, read_json


def proposal_for_fixture(scene_type="room",supported=True):
    scene,state=room_fixture()
    candidates=[];landmarks=[]
    camera=scene.cameras[0]
    for obj in scene.objects:
        if obj.template_id in ('wall','floor','ceiling'):
            continue
        item=state.objects[obj.object_id]
        pos=item.pose.translation_m
        candidates.append(Candidate(candidate_key=obj.object_id,label=obj.label,category=obj.category,
                                    importance=obj.importance,template_id=obj.template_id,bbox_xywh=(.1,.1,.2,.2),
                                    dimensions_m=obj.dimensions_m,estimated_position_m=pos,yaw_degrees=0,
                                    support_candidate_key=item.pose.anchor_ref if item.pose.anchor_ref!='world' else None,
                                    floor_contact_xy=None,opening=obj.opening,visible_surface='visible front',features='controlled fixture',
                                    text_content=None,is_group=obj.representation=='group'))
        if obj.object_id in ('desk','window','shelf'):
            ow,od,oh=obj.dimensions_m
            for local in [(-ow/2,0,0),(ow/2,0,0),(ow/2,0,oh),(-ow/2,0,oh)]:
                xy=project([np.array(pos)+np.array(local)],camera.position_m,camera.target_m,camera.lens_mm,4/3)[0]
                landmarks.append(Landmark(candidate_key=obj.object_id,local_point_m=local,image_xy=tuple(xy)))
    return ObservationProposal(supported=supported,unsupported_reason=None if supported else 'inconsistent perspective fixture',
                               scene_type=scene_type,room_dimensions_m=(6,5,3),camera_position_m=camera.position_m,
                               camera_target_m=camera.target_m,lens_mm=22,candidates=candidates,landmarks=landmarks,
                               assumptions=['controlled test, not real AI'],unresolved=[],simplification='KEEP_SOURCE')


def manifest_image(tmp_path):
    data=io.BytesIO();Image.new('RGB',(640,480),'gray').save(data,format='PNG')
    store=Store(tmp_path)
    manifest=store.create('Controlled planning fixture',data.getvalue(),{'fixed':'architecture,furniture'})
    return store,manifest


def test_projection_constraints_and_reference_crops(tmp_path):
    store,manifest=manifest_image(tmp_path)
    scene,states,assets=make_plan(manifest,store.project(manifest.project_id),proposal_for_fixture(),'job_test')
    assert scene.readiness=='review_ready'
    assert scene.projection_report['median_diagonal'] < .001
    assert len(scene.cameras)==4 and len(scene.references)==len(assets)
    window=next(o for o in scene.objects if o.template_id=='window')
    assert window.opening.rows==1 and window.opening.columns_per_row==[2]
    assert 'placement' in window.field_locks
    assert content_hash(states[0].objects)==content_hash(new_state(scene,states[0],'翌日',[]).objects)


def test_classroom_source_state_and_baseline_separate(tmp_path):
    store,manifest=manifest_image(tmp_path)
    scene,states,_=make_plan(manifest,store.project(manifest.project_id),proposal_for_fixture('教室'),'job_test')
    assert len(states)==2 and scene.source_state_id != scene.baseline_state_id
    desk=next(o for o in scene.objects if o.template_id=='desk')
    assert states[0].objects[desk.object_id].pose != states[1].objects[desk.object_id].pose


def test_hard_image_stays_reference_only(tmp_path):
    store,manifest=manifest_image(tmp_path)
    scene,states,_=make_plan(manifest,store.project(manifest.project_id),proposal_for_fixture(supported=False),'job_test')
    assert scene.readiness=='reference_only' and scene.simplification=='REFERENCE_ONLY'
    assert len(scene.references)>0


def test_job_failure_persisted_missing_api_and_resume(tmp_path,monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    store,manifest=manifest_image(tmp_path)
    manager=JobManager(store)
    jid=manager.enqueue(manifest.project_id)
    run_job(str(store.root),manifest.project_id,jid)
    failed=manager.job(manifest.project_id,jid)
    assert failed['status']=='failed' and failed['error']['code']=='AI_UNAVAILABLE'
    assert failed['api_attempts']==0
    manager.resume(manifest.project_id,jid)
    assert manager.job(manifest.project_id,jid)['status']=='queued'
    manager.cancel(manifest.project_id,jid)
    assert manager.job(manifest.project_id,jid)['status']=='cancelled'


def test_known_measure_scales_geometry_without_changing_projection(tmp_path):
    store,manifest=manifest_image(tmp_path)
    manifest.answers.update(measure_object='机',measure_width_m=2.6)
    scene,states,_=make_plan(manifest,store.project(manifest.project_id),proposal_for_fixture(),'job_test')
    desk=next(o for o in scene.objects if o.template_id=='desk')
    assert desk.dimensions_m[0]==2.6 and scene.room_dimensions_m==(12,10,6)
    assert desk.attribute_evidence['dimensions.width_m'].basis=='user_specified'
    assert scene.projection_report['median_diagonal'] < .001


def test_cancel_only_terminates_owned_process(tmp_path):
    import subprocess,sys,psutil
    from manga_blendy.jobs import update_job
    store,manifest=manifest_image(tmp_path)
    manager=JobManager(store);jid=manager.enqueue(manifest.project_id)
    owned=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    unrelated=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    try:
        path=store.project(manifest.project_id)/f'jobs/{jid}/job.json'
        update_job(path,status='analyzing',worker_pid=owned.pid,worker_created=psutil.Process(owned.pid).create_time())
        manager.cancel(manifest.project_id,jid)
        assert owned.wait(timeout=5) is not None
        assert unrelated.poll() is None
        assert manager.job(manifest.project_id,jid)['status']=='cancelled'
    finally:
        for process in (owned,unrelated):
            if process.poll() is None:process.terminate()
            process.wait(timeout=5)

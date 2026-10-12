import io
import zipfile

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from manga_blendy.domain import *
from manga_blendy.storage import *
from manga_blendy.revisions import revise
from fixtures import room_fixture


def source_bytes():
    stream=io.BytesIO()
    Image.new("RGB",(64,64),(120,120,120)).save(stream,format="PNG")
    return stream.getvalue()


def prepare(tmp_path):
    store=Store(tmp_path)
    manifest=store.create("日本語の部屋",source_bytes(),{})
    scene,state=room_fixture(manifest.assets[0].asset_id)
    root=store.project(manifest.project_id)
    directory=root/"jobs/job_test"
    directory.mkdir(parents=True)
    atomic_json(directory/"job.json",{"status":"succeeded"})
    atomic_json(directory/"candidate_scene.json",scene)
    atomic_json(directory/"candidate_states.json",[state.model_dump(mode="json")])
    atomic_json(directory/"assets.json",[])
    atomic_json(directory/"validation.json",{"test":True})
    store.adopt(manifest.project_id,"job_test",None)
    return store,manifest,scene,state


def test_next_day_inherits_every_object():
    scene,state=room_fixture()
    later=new_state(scene,state,"翌日",[])
    assert later.objects == state.objects and later.state_id != state.state_id
    assert later.parent_state_id == state.state_id


def test_moving_support_carries_book_and_preserves_other_objects():
    scene,state=room_fixture()
    before,_=world_pose(scene,state,"book")
    changed=new_state(scene,state,"動作後",[{"op":"Move","object_id":"desk","value":{"translation_m":[4,3.5,0]},"reason":"机を移動"}])
    after,_=world_pose(scene,changed,"book")
    np.testing.assert_allclose(after-before,[.5,0,0])
    assert changed.objects["bag"] == state.objects["bag"]
    assert state.objects["desk"].pose.translation_m == (3.5,3.5,0)


def test_architecture_lock_and_structural_changes_are_rejected():
    scene,state=room_fixture()
    with pytest.raises(DomainError,match="placement"):
        new_state(scene,state,"x",[{"op":"Move","object_id":"window","value":{"translation_m":[0,0,0]}}])
    with pytest.raises(DomainError,match="new revision"):
        new_state(scene,state,"x",[{"op":"ChangeDimensions","object_id":"bag","value":[1,1,1]}])


def test_anchor_cycle_rejected():
    scene,state=room_fixture()
    state.objects["desk"].pose.anchor_ref="book"
    with pytest.raises(DomainError,match="cyclic"):
        validate_state(scene,state)


def test_presence_does_not_mean_camera_visibility():
    scene,state=room_fixture()
    stored=new_state(scene,state,"片付け",[{"op":"SetPresence","object_id":"bag","value":{"presence":"stored","container_object_id":"shelf"}}])
    assert stored.objects["bag"].pose is None
    assert stored.objects["bag"].container_object_id == "shelf"
    with pytest.raises(ValidationError):
        ObjectState(presence="absent",pose=Pose(translation_m=(0,0,0)))


def test_unknown_variant_and_opening_unsupported():
    scene,state=room_fixture()
    with pytest.raises(DomainError):
        new_state(scene,state,"x",[{"op":"SetVariant","object_id":"book","value":"unregistered"}])
    with pytest.raises(DomainError):
        new_state(scene,state,"x",[{"op":"SetOpenAmount","object_id":"bag","value":.5}])


def test_revision_promotion_keeps_previous_revision_and_ids():
    scene,state=room_fixture()
    revised,snapshots=revise(scene,[state],[{"object_id":"posters","promote_group":True}])
    promoted=next(o for o in revised.objects if o.derived_from_group=="posters")
    assert promoted.object_id != "posters" and promoted.representation == "template"
    assert "posters" not in snapshots[0].objects
    assert "posters" in state.objects
    assert revised.revision_id != scene.revision_id
    assert next(o for o in revised.objects if o.object_id=="window").opening == scene.objects[5].opening


def test_adoption_idempotent_and_old_state_immutable(tmp_path):
    store,manifest,scene,state=prepare(tmp_path)
    before=(store.project(manifest.project_id)/f"states/{state.state_id}.json").read_bytes()
    store.adopt(manifest.project_id,"job_test",None)
    assert len(store.manifest(manifest.project_id).adoption_records)==1
    later=new_state(scene,state,"翌日",[])
    store.save_state(manifest.project_id,later,state.state_id)
    assert (store.project(manifest.project_id)/f"states/{state.state_id}.json").read_bytes()==before
    assert store.state(manifest.project_id,later.state_id).objects==state.objects
    with pytest.raises(DomainError,match="changed"):
        store.save_state(manifest.project_id,new_state(scene,state,"stale",[]),state.state_id)


def test_export_import_movable_and_source_unchanged(tmp_path):
    store,manifest,scene,state=prepare(tmp_path/"one")
    original=safe_path(store.project(manifest.project_id),manifest.assets[0].relative_path).read_bytes()
    archive=store.export(manifest.project_id)
    with zipfile.ZipFile(archive) as z:
        assert not any("jobs/" in n or "config" in n or "secret" in n for n in z.namelist())
    moved=Store(tmp_path/"moved")
    pid=moved.import_zip(archive.read_bytes())
    assert moved.state(pid,state.state_id).objects==state.objects
    assert moved.revision(pid,scene.revision_id)==scene
    assert safe_path(moved.project(pid),manifest.assets[0].relative_path).read_bytes()==original


@pytest.mark.parametrize("name",["../outside.txt","/outside.txt","C:/outside.txt","assets/../../x"])
def test_zip_traversal_rejected(tmp_path,name):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w") as z:z.writestr(name,"bad")
    with pytest.raises(DomainError):Store(tmp_path).import_zip(stream.getvalue())
    assert list(tmp_path.iterdir())==[]


def test_invalid_image_rejected(tmp_path):
    with pytest.raises(DomainError):Store(tmp_path).create("bad",b"not an image",{})


def test_finite_geometry_and_window_partition_contract():
    with pytest.raises(ValidationError):Pose(translation_m=(float('nan'),0,0))
    with pytest.raises(ValidationError):Opening(host_wall="back",rows=2,columns_per_row=[2])


def test_open_amount_preserves_partition_and_unknown_mechanism_is_blocked():
    scene,state=room_fixture()
    opened=new_state(scene,state,"窓を開ける",[{"op":"SetOpenAmount","object_id":"window","value":.5}])
    assert opened.objects["window"].open_amount==.5
    assert next(o for o in scene.objects if o.object_id=="window").opening.columns_per_row==[2]
    next(o for o in scene.objects if o.object_id=="window").opening.opening_method=None
    with pytest.raises(DomainError,match="未確認"):
        new_state(scene,state,"x",[{"op":"SetOpenAmount","object_id":"window","value":.5}])


def test_window_unequal_ratios_validation():
    opening=Opening(host_wall="back",rows=2,columns_per_row=[2,3],row_height_ratios=[.3,.7],column_width_ratios=[[.4,.6],[.2,.4,.4]])
    assert opening.rows==2
    with pytest.raises(ValidationError):Opening(host_wall="back",rows=1,columns_per_row=[2],column_width_ratios=[[.6,.6]])

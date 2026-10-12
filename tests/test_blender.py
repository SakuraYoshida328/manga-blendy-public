import os

import pytest
from PIL import Image

from manga_blendy.blender_adapter import render
from manga_blendy.config import load_config
from manga_blendy.storage import read_json
from fixtures import room_fixture


@pytest.mark.skipif(os.environ.get("MANGA_BLENDY_BLENDER_TEST")!="1",reason="real Blender opt-in integration")
def test_real_blender_four_views_reopen_and_id_stability(tmp_path):
    scene,state=room_fixture()
    from manga_blendy.material_catalog import classify_materials
    classify_materials(scene)
    config=load_config();config["render_samples"]=8
    output=render(scene,state,tmp_path,config)
    assert len(output["artifacts"])==4
    verify=read_json(tmp_path/"output/verify.json")
    assert verify["object_ids"]==sorted(state.objects)
    assert len(verify["camera_ids"])==4
    assert verify["openings"]["window"]=={"rows":1,"columns_per_row":[2]}
    assert verify['material_components']['bed']['frame']=='standard_furniture'
    assert verify['material_components']['bed']['sheet']=='fabric_furniture'
    assert verify['material_components']['bed']['pillow']=='fabric_furniture'
    for image in output["artifacts"]:
        with Image.open(tmp_path/"output"/image["path"]) as im:
            assert im.mode=="L" and im.size==(384,288)
    again=render(scene,state,tmp_path,config)
    assert again["input_hash"]==output["input_hash"]
    assert again["object_ids"]==output["object_ids"]

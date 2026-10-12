from manga_blendy.domain import *


def room_fixture(source_asset_id="fixture_source", resolution=(384, 288)):
    objects, items = [], {}
    rid, sid = uid("rev"), uid("state")
    def add(key, label, template, dims, pos, category=Category.FURNITURE, **extra):
        obj = SceneObject(object_id=key, label=label, category=category,
                          importance="structural" if category == Category.ARCHITECTURE else "ambient",
                          template_id=template, dimensions_m=dims, **extra)
        objects.append(obj)
        items[key] = ObjectState(presence="placed", pose=Pose(translation_m=pos))
    add("floor", "床", "floor", (6,5,.1), (3,2.5,-.1), Category.ARCHITECTURE, change_policy="locked")
    add("back", "奥壁", "wall", (6,.12,3), (3,5,0), Category.ARCHITECTURE, change_policy="locked")
    add("front", "手前壁", "wall", (6,.12,3), (3,0,0), Category.ARCHITECTURE, change_policy="locked")
    add("left", "左壁", "wall", (.12,5,3), (0,2.5,0), Category.ARCHITECTURE, change_policy="locked")
    add("right", "右壁", "wall", (.12,5,3), (6,2.5,0), Category.ARCHITECTURE, change_policy="locked")
    add("window", "一段二枚窓", "window", (1.6,.12,1.4), (4.6,5,1), Category.ARCHITECTURE,
        opening=Opening(host_wall="back", rows=1, columns_per_row=[2], opening_method="sliding"), field_locks=["geometry","placement"])
    add("door", "ドア", "door", (.85,.12,2.1), (6,1.3,0), Category.ARCHITECTURE,
        opening=Opening(host_wall="right", rows=1, columns_per_row=[1], opening_method="hinged"), field_locks=["geometry","placement"])
    add("desk", "机", "desk", (1.3,.7,.75), (3.5,3.5,0))
    add("chair", "椅子", "chair", (.45,.45,.85), (3.5,2.7,0))
    add("bed", "ベッド", "bed", (1.2,2.1,.8), (1,3.4,0))
    add("shelf", "本棚", "shelf", (.9,.35,1.8), (5.3,4.4,0))
    add("book", "重要な本", "box", (.25,.18,.035), (0,0,0), Category.PROP)
    items["book"].pose = Pose(translation_m=(0,0,0), anchor_ref="desk", anchor_key="top")
    add("bag", "鞄", "box", (.32,.22,.4), (4.6,2.2,0), Category.PROP)
    add("posters", "紙の装飾群", "decoration", (.7,.035,.45), (2.5,4.92,1.5), Category.DECORATION, representation="group")
    add("ceiling", "天井", "ceiling", (6,5,.1), (3,2.5,3), Category.ARCHITECTURE, change_policy="locked")
    cameras = [CameraPreset(camera_id=key, label=label, position_m=pos, target_m=target,
                            lens_mm=22, resolution=resolution)
               for key,label,pos,target in [
                   ("source","基準",(.65,.65,1.7),(3.2,3.6,1.2)),
                   ("left","左",(.4,2,1.7),(3.4,3.5,1.1)),
                   ("right","右",(5.5,.6,1.7),(2.7,3.5,1.1)),
                   ("high","高所",(3,.5,2.6),(3,3.5,.8))]]
    scene = SceneRevision(revision_id=rid, scene_id=uid("scene"), source_asset_id=source_asset_id,
                          master_image=MasterImage(asset_id=source_asset_id,source_asset_id=source_asset_id),
                          scene_type="開発用合成一室（AI解析なし）", usage_purpose="Blender結合試験",
                          room_dimensions_m=(6,5,3), objects=objects, references=[], cameras=cameras,
                          source_state_id=sid,baseline_state_id=sid,
                          assumptions=[Assumption(assumption_id=uid("assumption"),target_id=rid,field_path="all",
                                      proposed_value="全配置は開発用の手定義データ",reason="AI解析を実行していない試験",impact="structural")])
    state = SceneState(state_id=sid,revision_id=rid,story_scene_id="test_story",story_time="試験の初日",objects=items)
    return scene,state

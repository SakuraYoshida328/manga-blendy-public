"""Runs in an owned background Blender, never in the user's unsaved GUI scene."""
import bpy,json,hashlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import manga_saved,room_systems

def run(request):
    source=Path(request['source_blend']);out=Path(request['output_dir']);image_path=Path(request['image'])
    if hashlib.sha256(source.read_bytes()).hexdigest()!=request['source_sha256']:raise RuntimeError('source changed')
    if hashlib.sha256(image_path.read_bytes()).hexdigest()!=request['image_sha256']:raise RuntimeError('exterior image changed')
    bpy.ops.wm.open_mainfile(filepath=str(source),use_scripts=False)
    scene=bpy.context.scene;before=manga_saved.digest(scene)
    camera_before={o.get('mb_camera_id'):(list(o.matrix_world),o.data.lens) for o in scene.objects if o.type=='CAMERA'}
    spec=request['scene']['room_systems']
    image=bpy.data.images.load(str(image_path),check_existing=False);image.name=spec['exterior_packed_image'];image.pack()
    image.filepath='//exterior/'+image_path.name
    existing=[n for n in scene.world.node_tree.nodes if n.bl_idname=='ShaderNodeTexImage' and n.name.startswith('MB_SourceOnlyExterior')]
    if existing:
        for node in existing:node.image=image
    else:room_systems.source_only_world(scene,image,spec['exterior_angular_width'],spec['exterior_angular_height'],spec['exterior_vertical_center'])
    scene['mb_exterior_mode']='source_image_only';scene['mb_room_systems']=json.dumps(spec)
    scene['mb_revision_id']=request['scene']['revision_id'];scene['mb_state_id']=request['scene']['source_state_id']
    scene.view_layers[0].update();after=manga_saved.digest(scene)
    camera_after={o.get('mb_camera_id'):(list(o.matrix_world),o.data.lens) for o in scene.objects if o.type=='CAMERA'}
    if before!=after or camera_before!=camera_after:raise RuntimeError('interior geometry/camera changed')
    validation=room_systems.validate(scene,request['scene'])
    target=out/'lightweight.blend';bpy.ops.wm.save_as_mainfile(filepath=str(target),check_existing=False)
    result={'geometry_before':before,'geometry_after':after,'cameras_unchanged':True,'room_systems_validation':validation,
        'source_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==request['source_sha256'],
        'output_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    (out/'exterior_update_result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')

if __name__=='__main__':run(json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text(encoding='utf-8')))

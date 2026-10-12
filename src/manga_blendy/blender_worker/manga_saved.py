"""Render a prepared scene via Blender MCP. Plain bpy module, no AI/SDK imports."""
import bpy,json,hashlib,math,time,shutil
from pathlib import Path
from mathutils import Vector,Quaternion

def digest(scene):
    rows=[]
    for o in scene.objects:
        if o.type in {'CAMERA','LIGHT'}:continue
        vertices=[tuple(round(float(v),6) for v in pt.co) for pt in o.data.vertices] if o.type=='MESH' else []
        curves=[[tuple(round(float(v),6) for v in pt.co) for pt in s.points] for s in o.data.splines] if o.type=='CURVE' else []
        faces=[tuple(p.vertices) for p in o.data.polygons] if o.type=='MESH' else []
        mods=[(m.type,[(key,getattr(m,key)) for key in ('levels','render_levels','factor','iterations','width','thickness') if hasattr(m,key)]) for m in o.modifiers]
        # Blender adds name suffixes when a saved scene is appended repeatedly. Names are not spatial identity.
        row=(o.get('mb_id'),[round(float(v),6) for r in o.matrix_world for v in r],vertices,curves,faces,mods,o.hide_render)
        rows.append(json.dumps(row,ensure_ascii=False))
    return hashlib.sha256(json.dumps(sorted(rows),ensure_ascii=False).encode()).hexdigest()

def apply_style(scene,profile):
    """Line width and surface detail are independent; neither removes an object."""
    groups={}
    for key in ('main','fabric','detail'):
        col=bpy.data.collections.new('MB_RenderLines_'+key);scene.collection.children.link(col);groups[key]=col
    for o in scene.objects:
        if o.type not in {'MESH','CURVE'} or o.hide_render:continue
        role=o.get('mb_line_role','main')
        key='detail' if role=='detail' else 'fabric' if o.get('mb_material_class') in {'fabric_furniture','fabric_prop'} else 'main'
        groups[key].objects.link(o)
        if o.type=='CURVE' and role=='detail':
            # Appearance strokes use lighter ink, not deletion or changes to furniture geometry.
            for slot in o.material_slots:
                if slot.material:
                    mat=slot.material.copy();slot.material=mat
                    gray=1-profile['detail_strength']*.80
                    if mat.use_nodes:
                        for n in mat.node_tree.nodes:
                            if n.bl_idname in {'ShaderNodeBsdfDiffuse','ShaderNodeEmission'}:
                                inp=n.inputs.get('Color')
                                if inp:
                                    for link in list(inp.links):mat.node_tree.links.remove(link)
                                    inp.default_value=(gray,gray,gray,1)
    # Source-linked surface materials expose these controls without changing spatial state.
    mats={m for o in scene.objects if o.type in {'MESH','CURVE'} for m in o.data.materials if m}
    for m in mats:
        if not m.use_nodes:continue
        for n in m.node_tree.nodes:
            if n.name=='MB_DETAIL_STRENGTH':
                floor=n.get('mb_detail_floor',0);n.inputs[0].default_value=floor+(1-floor)*profile['detail_strength']
            if n.name=='MB_TONE_STRENGTH':n.inputs[0].default_value=.12*profile['tone_strength']
            if n.name=='ToneX':n.inputs[1].default_value=profile['resolution'][0]/3.4
            if n.name=='ToneY':n.inputs[1].default_value=profile['resolution'][1]/3.4
    scene.render.use_freestyle=True;scene.render.line_thickness=1
    fs=scene.view_layers[0].freestyle_settings
    for ls in list(fs.linesets):fs.linesets.remove(ls)
    for key,col in groups.items():
        ls=fs.linesets.new('Manga '+key);ls.select_by_collection=True;ls.collection=col;ls.collection_negation='INCLUSIVE'
        ls.select_silhouette=True;ls.select_border=True;ls.select_contour=True;ls.select_crease=key=='main'
        ls.select_material_boundary=False;ls.select_suggestive_contour=False;ls.select_ridge_valley=False
        if ls.linestyle is None:ls.linestyle=bpy.data.linestyles.new('Manga '+key+' ink')
        line=ls.linestyle;line.color=(.10,.10,.10) if key=='fabric' else (.02,.02,.02)
        line.thickness=profile[{'main':'main_line_px','fabric':'fabric_line_px','detail':'detail_line_px'}[key]]
        line.alpha=.85 if key=='fabric' else profile['detail_strength'] if key=='detail' else 1
        variation=line.thickness_modifiers.new('gentle ink variation','NOISE')
        if hasattr(variation,'amplitude'):variation.amplitude=.16*line.thickness
        if hasattr(variation,'period'):variation.period=70
        if hasattr(variation,'seed'):variation.seed=612
    scene.render.resolution_x,scene.render.resolution_y=profile['resolution'];scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='BW';scene.render.image_settings.color_depth='8'

def root_poses(scene):
    return {o['mb_id']:{'position':list(o.matrix_world.translation),'quaternion':list(o.matrix_world.to_quaternion())} for o in scene.objects if o.get('mb_root')}

def run(request):
    source=Path(request['source_blend']);out=Path(request['output_dir']);out.mkdir(parents=True,exist_ok=True)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=request['source_blend_sha256']:raise RuntimeError('saved scene checksum changed')
    window=bpy.context.window or bpy.context.window_manager.windows[0]
    original=window.scene;original_hash=digest(original)
    owned_types=('objects','collections','meshes','curves','materials','images','linestyles','cameras','lights','worlds','node_groups')
    existing={key:set(getattr(bpy.data,key)) for key in owned_types}
    started=time.monotonic();loaded=None;snapshot=None
    try:
        library=source
        if bpy.data.filepath and Path(bpy.data.filepath).resolve()==source.resolve():
            # Blender cannot append the currently open file. A temporary copy preserves its user's unsaved scene.
            snapshot=out/'_mcp_scene_snapshot.blend';shutil.copyfile(source,snapshot);library=snapshot
        with bpy.data.libraries.load(str(library),link=False) as (src,dst):dst.scenes=[src.scenes[0]]
        loaded=dst.scenes[0];window.scene=loaded
        roots={o['mb_id']:o for o in loaded.objects if o.get('mb_root')}
        catalog={o['object_id']:o for o in request['scene']['objects']};state=request['state']
        for group in request['scene'].get('detail_groups',[]):
            if any(state['objects'][oid]['presence']!='placed' or state['objects'][oid]['variant_id']!='default' for oid in group['member_object_ids']):
                raise RuntimeError('reference-backed group needs a prepared revision for member changes')
        if set(roots)!=set(catalog):raise RuntimeError('saved scene/object catalog mismatch')
        def resolve(oid):
            item=state['objects'][oid];pose=item['pose'];p=Vector(pose['translation_m']);q=Quaternion(pose['quaternion_wxyz'])
            if pose['anchor_ref']!='world':
                pp,qq=resolve(pose['anchor_ref']);offset=Vector((0,0,catalog[pose['anchor_ref']]['dimensions_m'][2] if pose['anchor_key']=='top' else 0));p=pp+qq@(p+offset);q=qq@q
            return p,q
        # Make roots independent after resolving the native support graph. Mesh parts still inherit their owner's root.
        for root in roots.values():root.parent=None
        for oid,root in roots.items():
            item=state['objects'][oid]
            if item['variant_id']!=root.get('mb_variant_id','default'):raise RuntimeError('unprepared appearance variant')
            root.hide_render=item['presence']!='placed'
            if item['presence']=='placed':
                p,q=resolve(oid);root.location=p;root.rotation_mode='QUATERNION';root.rotation_quaternion=q
                if root.get('mb_overlay_only'):raise RuntimeError('retired prop requires an explicitly registered overlay')
            def visibility(parent):
                for child in parent.children:
                    if child.get('mb_root'):continue
                    child.hide_render=root.hide_render;visibility(child)
            visibility(root)
            if item['open_amount']:
                hinges=[o for o in root.children if o.get('mb_door_hinge')]
                if catalog[oid]['template_id']!='door' or not hinges:raise RuntimeError('saved geometry has no prepared opening mechanism')
                hinges[0].rotation_euler.z=item['open_amount']*math.pi/2
        cam_spec=request['camera'];cameras={o['mb_camera_id']:o for o in loaded.objects if o.type=='CAMERA' and o.get('mb_camera_id')}
        camera=cameras[cam_spec['camera_id']];camera.location=cam_spec['position_m'];camera.rotation_euler=(Vector(cam_spec['target_m'])-camera.location).to_track_quat('-Z','Y').to_euler()
        camera.data.lens=cam_spec['lens_mm'];camera.data.shift_x=cam_spec['shift_x'];camera.data.shift_y=cam_spec['shift_y'];loaded.camera=camera
        camera.data.type='ORTHO' if cam_spec['projection']=='orthographic' else 'PERSP';camera.data.ortho_scale=cam_spec['ortho_scale_m']
        camera.data.sensor_width=cam_spec['sensor_width_mm'];camera.data.sensor_fit='HORIZONTAL';camera.data.clip_start,camera.data.clip_end=cam_spec['clip']
        loaded['mb_revision_id']=request['scene']['revision_id'];loaded['mb_state_id']=state['state_id'];loaded['mb_input_hash']=request['input_hash']
        apply_style(loaded,request['profile']);loaded.cycles.samples=request.get('samples',24)
        loaded.view_layers[0].update()
        import room_systems
        import importlib
        importlib.reload(room_systems)
        fixture_validation=room_systems.validate(loaded,request['scene'])
        before=digest(loaded)
        path=out/(cam_spec['camera_id']+'.png');loaded.render.filepath=str(path);bpy.ops.render.render(scene=loaded.name,write_still=True)
        after=digest(loaded)
        if before!=after:raise RuntimeError('render changed geometry or placement')
        result={'contract':'manga-blendy.background-result','schema_version':1,'status':'succeeded','input_hash':request['input_hash'],
            'revision_id':request['scene']['revision_id'],'state_id':state['state_id'],'camera_id':cam_spec['camera_id'],'profile':request['profile'],
            'artifacts':[{'camera_id':cam_spec['camera_id'],'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'resolution':request['profile']['resolution']}],
            'object_ids':sorted(roots),'geometry_hash_before':before,'geometry_hash_after':after,'root_poses':root_poses(loaded),
            'presence':{oid:state['objects'][oid]['presence'] for oid in roots},'blender_version':bpy.app.version_string,'backend':'existing_blender_mcp',
            'window_openings':{oid:{'rows':o['mb_opening_rows'],'columns_per_row':json.loads(o['mb_opening_columns'])} for oid,o in roots.items() if 'mb_opening_rows' in o},
            'samples':loaded.cycles.samples,'elapsed_seconds':round(time.monotonic()-started,2),'source_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==request['source_blend_sha256']}
        result['room_systems_validation']=fixture_validation
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('MANGA_SAVED_RENDER_COMPLETED '+cam_spec['camera_id'],flush=True)
        return result
    finally:
        window.scene=original
        if digest(original)!=original_hash:raise RuntimeError('MCP user scene changed')
        if loaded:
            # Freestyle retains collection references; release those before removing the temporary scene.
            for ls in loaded.view_layers[0].freestyle_settings.linesets:ls.collection=None
            bpy.data.scenes.remove(loaded)
            # Only data created by this append/render are eligible. Never purge the user's existing data.
            for key in owned_types:
                database=getattr(bpy.data,key)
                for item in list(database):
                    if item not in existing[key]:database.remove(item,do_unlink=True)
        if snapshot:snapshot.unlink(missing_ok=True)

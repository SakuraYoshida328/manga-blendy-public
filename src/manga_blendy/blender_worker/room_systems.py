"""Reusable prepared-room fixture and exterior checks, no account/network code.

The checks use the saved physical scene, independent of camera or manga mode.
"""
import hashlib, math

def bounds_gap(a,b):
    gaps=[max(0,b[0][i]-a[1][i],a[0][i]-b[1][i]) for i in range(3)]
    return math.sqrt(sum(g*g for g in gaps))

def overlaps(a,b):
    return all(a[0][i]<b[1][i] and b[0][i]<a[1][i] for i in range(3))

def children(root):
    for child in root.children:
        if child.get('mb_root'):continue
        yield child
        yield from children(child)

def bounds(objects):
    from mathutils import Vector
    points=[obj.matrix_world@Vector(p) for obj in objects if obj.type in {'MESH','CURVE'} and not obj.hide_render for p in obj.bound_box]
    if not points:raise RuntimeError('fixture has no physical parts')
    return ([min(p[i] for p in points) for i in range(3)],[max(p[i] for p in points) for i in range(3)])

def validate(scene,catalog):
    spec=catalog.get('room_systems')
    if not spec:return {'status':'not_configured','checks':[]}
    roots={o['mb_id']:o for o in scene.objects if o.get('mb_root')}
    height=catalog['room_dimensions_m'][2];checks=[]
    lid=spec.get('flush_ceiling_light_id')
    if lid:
        parts=list(children(roots[lid]));box=bounds(parts)
        if any(p.get('mb_suspension') or 'pendant wire' in p.name.lower() for p in parts):raise RuntimeError('flush LED cannot have a suspension axis')
        if abs(box[1][2]-height)>.006 or height-box[0][2]>.095:raise RuntimeError('LED ceiling mount is not flush')
        checks.append({'kind':'flush_led','bounds_m':box,'no_suspension':True})
    aid,cid=spec.get('air_conditioner_id'),spec.get('curtain_id')
    if aid and cid:
        ac=roots[aid];a=bounds([p for p in children(ac) if p.get('mb_component_id')=='ac_body'])
        c=bounds(list(children(roots[cid])));gap=bounds_gap(a,c)
        if gap<spec['minimum_ac_curtain_gap_m']:raise RuntimeError('AC intersects curtain operating envelope')
        from mathutils import Vector
        front=ac.matrix_world.to_quaternion()@Vector((0,-1,0));distance=spec['ac_front_service_m']
        service=([a[0][i]+min(0,front[i]*distance) for i in range(3)],
                 [a[1][i]+max(0,front[i]*distance) for i in range(3)])
        if overlaps(service,c):raise RuntimeError('AC service/airflow envelope intersects curtains')
        if a[1][2]>height-.03:raise RuntimeError('AC top inspection clearance is insufficient')
        checks.append({'kind':'ac_curtain_clearance','gap_m':gap,'ac_bounds_m':a,'curtain_bounds_m':c,
                       'front_service_m':distance,'service_clear':True,'ceiling_gap_m':height-a[1][2],
                       'basis':'provisional operating envelopes, not measured installation data'})
    if spec.get('exterior_mode')=='source_image_only':
        forbidden=[o.name for o in scene.objects if o.get('mb_exterior_scope')=='distant_geometry' or
            (o.parent and o.parent.name.startswith('Exterior_estimated') and any(t in o.name.lower() for t in ('distant house','distant window','roof','tree trunk','tree leaf')))]
        if forbidden:raise RuntimeError('source-image exterior contains distant 3D geometry')
        image=next((im for im in __import__('bpy').data.images if im.name==spec['exterior_packed_image'] or im.name.startswith(spec['exterior_packed_image']+'.')),None)
        if not image or not image.packed_file:raise RuntimeError('source exterior is not embedded')
        if hashlib.sha256(image.packed_file.data).hexdigest()!=spec['exterior_source_sha256']:raise RuntimeError('exterior source-image hash changed')
        checks.append({'kind':'source_image_exterior','distant_geometry_count':0,'embedded_sha256':spec['exterior_source_sha256'],
                       'camera_independent':True,'parallax':'none; distant 2D reference, no inferred geometry'})
    return {'status':'passed','checks':checks}

def source_only_world(scene,image,angle_width=1.15,angle_height=.80,vertical_center=.50):
    """Fixed ray-direction mapping of a source photograph; zero exterior meshes.

    Only camera/transparent rays see the image. Existing daylight stays unchanged.
    """
    import bpy
    world=scene.world;world.use_nodes=True;nt=world.node_tree
    out=next(n for n in nt.nodes if n.bl_idname=='ShaderNodeOutputWorld')
    old=out.inputs['Surface'].links[0].from_socket
    coords=nt.nodes.new('ShaderNodeTexCoord')
    split=nt.nodes.new('ShaderNodeSeparateXYZ');nt.links.new(coords.outputs['Normal'],split.inputs[0])
    def mathnode(op,a,b=None):
        n=nt.nodes.new('ShaderNodeMath');n.operation=op
        if isinstance(a,(int,float)):n.inputs[0].default_value=a
        else:nt.links.new(a,n.inputs[0])
        if b is not None:
            if isinstance(b,(int,float)):n.inputs[1].default_value=b
            else:nt.links.new(b,n.inputs[1])
        return n.outputs[0]
    # World Normal is the incoming direction; rays into the rear window have -Y.
    denominator=mathnode('MAXIMUM',mathnode('ABSOLUTE',split.outputs['Y']),.05)
    u=mathnode('SUBTRACT',.5,mathnode('DIVIDE',mathnode('DIVIDE',split.outputs['X'],denominator),angle_width))
    v=mathnode('SUBTRACT',vertical_center,mathnode('DIVIDE',mathnode('DIVIDE',split.outputs['Z'],denominator),angle_height))
    vec=nt.nodes.new('ShaderNodeCombineXYZ');nt.links.new(u,vec.inputs['X']);nt.links.new(v,vec.inputs['Y'])
    tex=nt.nodes.new('ShaderNodeTexImage');tex.name='MB_SourceOnlyExterior';tex.image=image;tex.extension='EXTEND';tex.interpolation='Linear'
    nt.links.new(vec.outputs[0],tex.inputs['Vector'])
    background=nt.nodes.new('ShaderNodeBackground');nt.links.new(tex.outputs['Color'],background.inputs['Color']);background.inputs['Strength'].default_value=1
    ray=nt.nodes.new('ShaderNodeLightPath');mix=nt.nodes.new('ShaderNodeMixShader');nt.links.new(ray.outputs['Is Camera Ray'],mix.inputs[0]);nt.links.new(old,mix.inputs[1]);nt.links.new(background.outputs[0],mix.inputs[2])
    nt.links.new(mix.outputs[0],out.inputs['Surface'])
    scene['mb_exterior_mode']='source_image_only';scene['mb_exterior_angular_width']=angle_width

"""Executed only by Blender's isolated Python; no app/SDK imports."""
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def run(request):
    output = Path(request["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    if request.get("mode") == "verify":
        bpy.ops.wm.open_mainfile(filepath=str(output / "lightweight.blend"), use_scripts=False)
        ids = sorted(o["mb_id"] for o in bpy.data.objects if o.get("mb_root"))
        cameras = sorted(o["mb_camera_id"] for o in bpy.data.objects if o.type == "CAMERA")
        result = {"object_ids": ids, "camera_ids": cameras, "state_id": bpy.context.scene["mb_state_id"],
                  "input_hash": bpy.context.scene["mb_input_hash"], "blender_version": bpy.app.version_string,
                  "openings": {o["mb_id"]: {"rows": o["mb_rows"], "columns_per_row": list(o["mb_columns_per_row"])}
                               for o in bpy.data.objects if o.get("mb_root") and "mb_rows" in o},
                  "surface_reference_ids": sorted({rid for ob in bpy.data.objects if ob.type == "MESH"
                      for rid in json.loads(ob.get("mb_surface_reference_ids", "[]"))}),
                  "object_reference_ids": {ob["mb_id"]:json.loads(ob.get("mb_reference_ids","[]"))
                                           for ob in bpy.data.objects if ob.get("mb_root")},
                  "packed_surface_images": sum(bool(im.packed_file) for im in bpy.data.images if im.get("mb_reference_id")),
                  'material_components':{oid:{ob.get('mb_component_id','body'):ob.get('mb_material_class') for ob in bpy.data.objects
                      if ob.type=='MESH' and ob.get('mb_id')==oid} for oid in ids}}
        (output / "verify.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return
    scene_data, state = request["scene"], request["state"]
    before = hash_value({"scene": scene_data, "state": state})
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if hasattr(bpy.context.preferences.filepaths,"use_save_preview"):
        bpy.context.preferences.filepaths.use_save_preview = False
    bpy.context.preferences.filepaths.save_version = 0
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = request.get("samples", 16)
    scene.cycles.use_denoising = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "BW"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = -0.5
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("MangaBlendyWorld")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (.65, .65, .65, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = .25
    materials = {}
    for name, shade in [("wall", .83), ("floor", .6), ("furniture", .38), ("paper", .93), ("frame", .15), ("glass", .72)]:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = (shade, shade, shade, 1)
        mat.use_nodes = True
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (shade, shade, shade, 1)
        mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = .8
        materials[name] = mat
    roots = {}
    catalog = {o["object_id"]: o for o in scene_data["objects"]}
    for obj in scene_data["objects"]:
        root = bpy.data.objects.new(obj["label"], None)
        scene.collection.objects.link(root)
        root["mb_id"] = obj["object_id"]
        root["mb_root"] = True
        root["mb_template"] = obj["template_id"]
        root["mb_reference_ids"] = json.dumps(obj["reference_ids"])
        root["mb_management_role"] = obj.get("management_role", "auto")
        root["mb_geometry_detail"] = obj.get("geometry_detail", "standard")
        root['mb_material_class']=obj.get('material_class') or 'standard_furniture'
        root['mb_variant_id']=state['objects'][obj['object_id']]['variant_id']
        roots[obj["object_id"]] = root

    def resolve(oid, visited=None):
        item = state["objects"][oid]
        if item["presence"] != "placed":
            return Vector((0, 0, 0)), Quaternion(), False
        pose = item["pose"]
        pos, rot = Vector(pose["translation_m"]), Quaternion(pose["quaternion_wxyz"])
        if pose["anchor_ref"] != "world":
            p, q, present = resolve(pose["anchor_ref"])
            offset = Vector((0, 0, catalog[pose["anchor_ref"]]["dimensions_m"][2] if pose["anchor_key"] == "top" else 0))
            pos, rot = p + q @ (pos + offset), q @ rot
        return pos, rot, True

    surface_materials, applied_refs = {}, set()
    def apply_surface(mesh, part):
        oid = mesh["mb_id"]
        component_id={'bed_mattress':'sheet','desk_top':'body','plant_pot':'pot'}.get(part,part)
        components={c['component_id']:c for c in catalog[oid].get('components',[])}
        component=components.get(component_id)
        kind=(component or {}).get('material_class') or catalog[oid].get('material_class') or 'standard_furniture'
        mesh['mb_component_id']=component_id;mesh['mb_material_class']=kind
        mesh['mb_line_role']='fabric' if kind in {'fabric_furniture','fabric_prop'} else 'main'
        if kind in {'fabric_furniture','fabric_prop'}:
            for polygon in mesh.data.polygons:polygon.use_smooth=True
        bindings = [s for s in request.get("surfaces", []) if s["object_id"] == oid and s["target_part"] == part]
        mesh["mb_part"] = part
        if not bindings:
            return
        grouped = {}
        for binding in bindings:
            grouped.setdefault(binding["face"], []).append(binding)
        normals = {"top":Vector((0,0,1)), "front":Vector((0,-1,0)), "back":Vector((0,1,0)),
                   "left":Vector((-1,0,0)), "right":Vector((1,0,0))}
        linked = []
        for face, details in grouped.items():
            polygons = [p for p in mesh.data.polygons if p.normal.dot(normals[face]) > .99]
            if not polygons:
                raise RuntimeError("mapped surface has no matching polygon")
            key = (oid,part,face)
            if key not in surface_materials:
                mat = mesh.data.materials[0].copy()
                mat.name = f"Detail_{oid}_{part}_{face}"
                for detail in details:
                    path = (output/detail["path"]).resolve()
                    if not path.is_relative_to(output.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=detail["sha256"]:
                        raise RuntimeError("surface path/hash invalid")
                    im = bpy.data.images.load(str(path),check_existing=False)
                    im.name = detail["reference_id"]; im["mb_reference_id"] = detail["reference_id"]
                    im.pack()
                    im.filepath = "//"+detail["path"]
                    tex = mat.node_tree.nodes.new("ShaderNodeTexImage"); tex.image = im
                    tex.extension = "REPEAT"; tex["mb_reference_id"] = detail["reference_id"]
                    uvnode = mat.node_tree.nodes.new("ShaderNodeUVMap"); uvnode.uv_map = detail["reference_id"]
                    mat.node_tree.links.new(uvnode.outputs["UV"],tex.inputs["Vector"])
                    shader = mat.node_tree.nodes.get("Principled BSDF")
                    if detail["usage"] == "color":
                        mat.node_tree.links.new(tex.outputs["Color"],shader.inputs["Base Color"])
                    else:
                        im.colorspace_settings.name = "Non-Color"
                        bump = mat.node_tree.nodes.new("ShaderNodeBump")
                        bump.inputs["Distance"].default_value = detail["bump_distance_m"]
                        bump.inputs["Strength"].default_value = .6
                        mat.node_tree.links.new(tex.outputs["Color"],bump.inputs["Height"])
                        mat.node_tree.links.new(bump.outputs["Normal"],shader.inputs["Normal"])
                surface_materials[key] = mat
            mesh.data.materials.append(surface_materials[key])
            for polygon in polygons:
                polygon.material_index = len(mesh.data.materials)-1
            w,d,h = mesh.dimensions
            for detail in details:
                uv = mesh.data.uv_layers.new(name=detail["reference_id"])
                angle = math.radians(detail["rotation_degrees"])
                for polygon in polygons:
                    for li in polygon.loop_indices:
                        x,y,z = mesh.data.vertices[mesh.data.loops[li].vertex_index].co
                        if face == "top": u,v = x/w+.5,y/d+.5
                        elif face in {"front","back"}: u,v = (x/w+.5 if face=="front" else .5-x/w),z/h+.5
                        else: u,v = (y/d+.5 if face=="left" else .5-y/d),z/h+.5
                        u,v = u-.5,v-.5
                        ru,rv = .5+u*math.cos(angle)-v*math.sin(angle),.5+u*math.sin(angle)+v*math.cos(angle)
                        uv.data[li].uv = (ru*detail["repeat_uv"][0],rv*detail["repeat_uv"][1])
                applied_refs.add(detail["reference_id"]); linked.append(detail["reference_id"])
        mesh["mb_surface_reference_ids"] = json.dumps(linked)

    def box(parent, dimensions, center, material="furniture", part="body"):
        bpy.ops.mesh.primitive_cube_add(size=1)
        mesh = bpy.context.object
        mesh.name = parent.name + ".part"
        mesh.parent = parent
        mesh.location = center
        mesh.dimensions = dimensions
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        mesh.data.materials.append(materials[material])
        mesh["mb_id"] = parent["mb_id"]
        if catalog[parent["mb_id"]].get("geometry_detail") != "proxy":
            bevel = mesh.modifiers.new("small edges", "BEVEL")
            bevel.width = min(min(dimensions)*.08, .008)
            bevel.segments = 1
        apply_surface(mesh,part)
        if mesh.get('mb_material_class') in {'fabric_furniture','fabric_prop'}:
            for modifier in mesh.modifiers:
                if modifier.type=='BEVEL':modifier.segments=3
        return mesh

    room_w, room_d, room_h = scene_data["room_dimensions_m"]
    openings = []
    for obj in catalog.values():
        if obj["opening"] and state["objects"][obj["object_id"]]["presence"] == "placed":
            pos, rot, _ = resolve(obj["object_id"])
            openings.append((obj, pos))
    for oid, obj in catalog.items():
        root = roots[oid]
        pos, rot, present = resolve(oid)
        root.location, root.rotation_mode, root.rotation_quaternion = pos, "QUATERNION", rot
        if not present or obj["representation"] == "reference_only":
            continue
        w, d, h = obj["dimensions_m"]
        template = obj["template_id"]
        if obj.get("geometry_detail") == "proxy":
            box(root,(w,d,h),(0,0,h/2))
        elif template == "wall":
            # Tile a wall around rectangular apertures rather than covering openings with a texture.
            horizontal = w > d
            span = w if horizontal else d
            wall_key = ("back" if pos.y > room_d/2 else "front") if horizontal else ("right" if pos.x > room_w/2 else "left")
            cuts = []
            for opening, op in openings:
                if opening["opening"]["host_wall"] == wall_key:
                    ow, _, oh = opening["dimensions_m"]
                    center = op.x-pos.x if horizontal else op.y-pos.y
                    cut = (max(-span/2, center-ow/2), min(span/2, center+ow/2), max(0, op.z), min(h, op.z+oh))
                    if cut[0] < cut[1] and cut[2] < cut[3]:
                        cuts.append(cut)
            xs = sorted(set([-span/2, span/2] + [v for c in cuts for v in c[:2]]))
            zs = sorted(set([0, h] + [v for c in cuts for v in c[2:]]))
            for x1, x2 in zip(xs, xs[1:]):
                for z1, z2 in zip(zs, zs[1:]):
                    x, z = (x1+x2)/2, (z1+z2)/2
                    if any(a <= x <= b and c <= z <= e for a, b, c, e in cuts):
                        continue
                    dims = (x2-x1, d, z2-z1) if horizontal else (w, x2-x1, z2-z1)
                    center = (x, 0, z) if horizontal else (0, x, z)
                    box(root, dims, center, "wall")
        elif template == "ceiling":
            box(root, (w, d, h), (0, 0, h/2), "wall")
        elif template == "floor":
            box(root, (w, d, h), (0, 0, h/2), "floor")
            for i in range(1, 10):
                box(root, (w, .004, .001), (0, -d/2+d*i/10, h+.001), "frame", part="floor_joint")
        elif template in ("window", "door"):
            opening = obj["opening"]
            root['mb_opening_rows']=opening['rows']
            root['mb_opening_columns']=json.dumps(opening['columns_per_row'])
            if opening["host_wall"] in ("left", "right"):
                root.rotation_quaternion = Quaternion((0, 0, 1), math.pi/2)
            thickness = min(.055, w/15)
            for x in (-w/2, w/2):
                box(root, (thickness, .15, h), (x, 0, h/2), "frame")
            for z in (0, h):
                box(root, (w, .15, thickness), (0, 0, z), "frame")
            amount = state["objects"][oid]["open_amount"]
            if template == "door":
                hinge = bpy.data.objects.new(root.name+".hinge", None)
                scene.collection.objects.link(hinge)
                hinge.parent = root
                hinge.location = (-w/2, 0, 0)
                if opening["opening_method"] == "sliding":
                    hinge.location.x += amount*w
                else:
                    hinge.rotation_euler.z = -amount*math.pi/2
                hinge["mb_id"] = oid
                box(hinge, (w-thickness*2, .055, h-thickness*2), (w/2, 0, h/2), "furniture")
            else:
                rows = opening["rows"]
                root["mb_rows"] = rows
                root["mb_columns_per_row"] = opening["columns_per_row"]
                row_ratios = opening.get("row_height_ratios") or [1/rows]*rows
                all_col_ratios = opening.get("column_width_ratios") or [[1/c]*c for c in opening["columns_per_row"]]
                for row, columns in enumerate(opening["columns_per_row"]):
                    cell_h = h*row_ratios[row]
                    z_start = h*sum(row_ratios[:row])
                    col_ratios = all_col_ratios[row]
                    for col in range(columns):
                        cell_w = w*col_ratios[col]
                        x_start = -w/2 + w*sum(col_ratios[:col])
                        x = x_start + cell_w/2
                        z = z_start + cell_h/2
                        if amount < .99:
                            box(root, (max(.01, cell_w-thickness)*(1-amount), .012, cell_h-thickness), (x+amount*cell_w/2, 0, z), "glass")
                        if col:
                            box(root, (thickness, .1, cell_h), (x_start, 0, z), "frame")
                    if row:
                        box(root, (w, .1, thickness), (0, 0, z_start), "frame")
        elif template == "desk":
            box(root, (w, d, .07), (0, 0, h-.035), part="desk_top")
            for x in (-w*.4, w*.4):
                for y in (-d*.4, d*.4):
                    box(root, (.06, .06, h-.07), (x, y, (h-.07)/2), "frame")
        elif template == "chair":
            seat = h*.5
            box(root, (w, d, .045), (0, 0, seat), part="seat")
            box(root, (w, .06, h-seat), (0, d*.43, (h+seat)/2))
            for x in (-w*.4, w*.4):
                for y in (-d*.4, d*.4):
                    box(root, (.04, .04, seat), (x, y, seat/2), "frame")
        elif template == "bed":
            box(root, (w, d, h*.35), (0, 0, h*.18), "frame",part='frame')
            box(root, (w*.97, d*.97, h*.3), (0, 0, h*.5), "paper", part="bed_mattress")
            box(root, (w*.75, d*.16, h*.12), (0, d*.32, h*.7), "paper",part='pillow')
            box(root, (w, .06, h), (0, d/2, h/2),part='headboard')
        elif template == "shelf":
            for x in (-w/2, w/2):
                box(root, (.04, d, h), (x, 0, h/2))
            box(root, (w, .035, h), (0, d/2, h/2), part="shelf_back")
            for i in range(5):
                box(root, (w, d, .04), (0, 0, h*i/4))
        elif template == "rug":
            box(root,(w,d,h),(0,0,h/2),"paper")
        elif template == "plant":
            box(root,(w*.45,d*.45,h*.28),(0,0,h*.14),part="plant_pot")
            for n in range(12):
                angle = n*math.pi*2/12
                bpy.ops.mesh.primitive_uv_sphere_add(segments=8,ring_count=4,radius=1)
                leaf = bpy.context.object; leaf.parent = root; leaf["mb_id"] = oid
                leaf.location = (math.cos(angle)*w*.25,math.sin(angle)*d*.25,h*(.5+.30*(n%3)/2))
                leaf.scale = (w*.22,d*.08,h*.2); leaf.rotation_euler.z = angle
                leaf.data.materials.append(materials["furniture"])
        elif template in {"phone","book","pen"}:
            if template == "book":
                box(root,(w,d,h*.08),(0,0,h*.96))
                box(root,(w,d,h*.08),(0,0,h*.04),part="bottom_cover")
            else:
                box(root,(w,d,h),(0,0,h/2))
            if template == "phone":
                box(root,(w*.82,d*.78,.001),(0,0,h+.0005),"frame",part="phone_screen")
                box(root,(w*.10,d*.02,.001),(0,-d*.44,h+.0005),"paper",part="button")
            elif template == "book":
                box(root,(w*.92,d*.94,h*.84),(0,0,h*.5),"paper",part="pages")
                box(root,(w*.08,d,h),(w*.46,0,h*.5),"frame",part="spine")
            else:
                box(root,(w*.7,d*.12,h*.7),(0,-d*.56,h*.5),"frame",part="tip")
                box(root,(w*.4,d*.22,h*.2),(w*.45,d*.3,h*.95),"paper",part="clip")
        else:
            variant = state["objects"][oid]["variant_id"]
            # Registered variants affect appearance, never arbitrary structural geometry.
            material = "paper" if template == "decoration" or variant != "default" else "furniture"
            box(root, (w, d, h), (0, 0, h/2), material)
    cameras = []
    for preset in scene_data["cameras"]:
        data = bpy.data.cameras.new(preset["label"])
        camera = bpy.data.objects.new(preset["label"], data)
        scene.collection.objects.link(camera)
        camera.location = preset["position_m"]
        camera.rotation_euler = (Vector(preset["target_m"])-camera.location).to_track_quat("-Z", "Y").to_euler()
        data.type = "ORTHO" if preset["projection"] == "orthographic" else "PERSP"
        data.lens = preset["lens_mm"]
        data.ortho_scale = preset["ortho_scale_m"]
        data.sensor_width = preset["sensor_width_mm"]
        data.sensor_fit = "HORIZONTAL"
        data.shift_x, data.shift_y = preset["shift_x"], preset["shift_y"]
        data.clip_start, data.clip_end = preset["clip"]
        camera["mb_camera_id"] = preset["camera_id"]
        cameras.append((camera, preset))
    light = bpy.data.lights.new("room area", "AREA")
    lamp = bpy.data.objects.new("room area", light)
    scene.collection.objects.link(lamp)
    lamp.location = (room_w/2, room_d/2, room_h-.1)
    light.energy, light.size = 100, max(room_w, room_d)*.65
    scene["mb_revision_id"] = scene_data["revision_id"]
    scene["mb_state_id"] = state["state_id"]
    scene["mb_input_hash"] = request["input_hash"]
    scene["mb_surface_bindings"] = json.dumps(request.get("surfaces",[]))
    if applied_refs != {s["reference_id"] for s in request.get("surfaces",[])}:
        raise RuntimeError("not all requested detail images were mapped")
    scene.camera = cameras[0][0]
    scene.render.resolution_x, scene.render.resolution_y = cameras[0][1]["resolution"]
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "lightweight.blend"))
    artifacts = []
    for camera, preset in cameras:
        scene.camera = camera
        scene.render.resolution_x, scene.render.resolution_y = preset["resolution"]
        scene.render.resolution_percentage = 100
        file = output / (preset["camera_id"] + ".png")
        scene.render.filepath = str(file)
        bpy.ops.render.render(write_still=True)
        artifacts.append({"camera_id": preset["camera_id"], "path": file.name,
                          "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                          "revision_id": scene_data["revision_id"], "state_id": state["state_id"]})
    after = hash_value({"scene": scene_data, "state": state})
    if before != after:
        raise RuntimeError("render mutated scene/state input")
    result = {"input_hash": request["input_hash"], "state_content_hash": hash_value(state),
              "object_ids": sorted(roots), "camera_ids": [p["camera_id"] for _, p in cameras],
              "blender_version": bpy.app.version_string, "artifacts": artifacts,
              "input_unchanged": before == after, "generator_version": "0.2.0-room-details",
              "surface_reference_ids": sorted(applied_refs), "surfaces": request.get("surfaces",[])}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--")+1:]
    run(json.loads(Path(arguments[0]).read_text(encoding="utf-8")))

"""Conservative layout checks; warnings never silently rearrange observed furniture."""
import numpy as np

from .domain import Category, world_pose


def layout_warnings(scene, state):
    w,d,h=scene.room_dimensions_m
    warnings=[]
    boxes=[]
    for obj in scene.objects:
        item=state.objects[obj.object_id]
        if item.presence!='placed' or obj.category==Category.ARCHITECTURE:
            continue
        pos,rot=world_pose(scene,state,obj.object_id)
        ow,od,oh=obj.dimensions_m
        corners=np.array([[x,y,z] for x in (-ow/2,ow/2) for y in (-od/2,od/2) for z in (0,oh)])
        transformed=rot.apply(corners)+pos
        low,high=transformed.min(axis=0),transformed.max(axis=0)
        if (low < [-.12,-.12,-.08]).any() or (high > [w+.12,d+.12,h+.12]).any():
            warnings.append(f'{obj.label}: 室内の推定境界からはみ出しています')
        if obj.template_id in ('desk','bed','shelf','box') and item.pose.anchor_ref=='world':
            boxes.append((obj,low,high))
    for i,(a,al,ah) in enumerate(boxes):
        for b,bl,bh in boxes[i+1:]:
            overlap=np.minimum(ah,bh)-np.maximum(al,bl)
            if (overlap > .05).all():
                warnings.append(f'{a.label}と{b.label}: 推定形状が交差しています')
    for opening in (o for o in scene.objects if o.template_id=='door'):
        pos,_=world_pose(scene,state,opening.object_id)
        host=opening.opening.host_wall
        axis=1 if host in ('back','front') else 0
        center=pos.copy();center[axis]+=(-.35 if host in ('back','right') else .35)
        for obj,low,high in boxes:
            if np.all(center[:2]>=low[:2]-.2) and np.all(center[:2]<=high[:2]+.2):
                warnings.append(f'{obj.label}: ドア前の動線が塞がれている可能性があります')
    return warnings

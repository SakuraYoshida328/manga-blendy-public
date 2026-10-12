"""Ambient books are reference-backed groups, without abandoning their identity records."""
from collections import defaultdict
from .domain import DetailGroup, Evidence

def group_ambient_books(scene,state):
    if scene.detail_groups:return scene
    groups=defaultdict(list)
    for obj in scene.objects:
        item=state.objects[obj.object_id]
        if obj.importance!='ambient' or item.presence!='placed' or item.pose.anchor_ref=='world':continue
        if obj.template_id!='book' and not any(w in obj.label.lower() for w in ('本 ','本棚の本','棚上段の本','作業台の本','book')):continue
        groups[(item.pose.anchor_ref,round(item.pose.translation_m[2],2))].append(obj)
    for (support,z),members in groups.items():
        if len(members)<3:continue
        gid=f'books_{len(scene.detail_groups):02d}'
        scene.detail_groups.append(DetailGroup(group_id=gid,label='棚・台の本のまとまり',support_object_id=support,
            member_object_ids=[o.object_id for o in members],estimated_count=len(members),
            count_evidence=Evidence(basis='estimated',reason='現在の管理ID数を仮冊数とする。元画像の実際の総冊数とは区別する。'),
            reference_ids=sorted({r for o in members for r in o.reference_ids})))
        for obj in members:obj.geometry_detail='proxy'
    return scene

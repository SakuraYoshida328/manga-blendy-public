"""Drawing-material dispatch, kept separate from identity, movement and time state."""
from .domain import Category, MaterialClass, ObjectComponent

MATERIAL_LABELS={"building":"建物","standard_furniture":"通常家具","fabric_furniture":"布家具",
                 "standard_prop":"通常小物","fabric_prop":"布小物"}
TEXTILES=("布団","シーツ","枕","カーテン","ラグ","カーペット","クッション","ソファ",
          "curtain","duvet","pillow","rug","cushion","sofa")
SOFT_PROPS=("服","上着","タオル","バッグ","鞄","ぬいぐるみ","cardigan","towel","bag","plush")

def inferred_material(obj):
    if obj.material_class is not None:return obj.material_class
    if obj.category==Category.ARCHITECTURE:return MaterialClass.BUILDING
    label=obj.label.lower()
    if any(word in label for word in SOFT_PROPS):return MaterialClass.FABRIC_PROP
    if obj.template_id=='rug' or any(word in label for word in TEXTILES):
        return MaterialClass.FABRIC_FURNITURE if obj.category==Category.FURNITURE or obj.management_role=='fixed_furniture' else MaterialClass.FABRIC_PROP
    if obj.category==Category.FURNITURE or obj.management_role=='fixed_furniture':return MaterialClass.STANDARD_FURNITURE
    return MaterialClass.STANDARD_PROP

def classify_materials(scene):
    """Explicit upgrade; merely reading an old revision never rewrites its files."""
    for obj in scene.objects:
        obj.material_class=inferred_material(obj)
        registered={part.component_id for part in obj.components}
        specs=[('body',obj.label,obj.material_class)]
        if obj.template_id=='bed':
            obj.material_class=MaterialClass.STANDARD_FURNITURE
            specs=[('frame','ベッド枠・収納',MaterialClass.STANDARD_FURNITURE),
                   ('sheet','マットレス・シーツ',MaterialClass.FABRIC_FURNITURE),
                   ('duvet','掛け布団',MaterialClass.FABRIC_FURNITURE),
                   ('pillow','枕',MaterialClass.FABRIC_FURNITURE),
                   ('blanket','足元の布',MaterialClass.FABRIC_FURNITURE),
                   ('headboard','ヘッドボード',MaterialClass.STANDARD_FURNITURE)]
        elif obj.template_id=='chair':
            # Wood/metal classroom chairs are not automatically upholstered.
            seat_kind=MaterialClass.FABRIC_FURNITURE if any(w in obj.label.lower() for w in ('布張','upholster','cushion')) else MaterialClass.STANDARD_FURNITURE
            specs=[('frame','椅子の構造',MaterialClass.STANDARD_FURNITURE),('seat','座面',seat_kind)]
        elif obj.template_id=='plant':
            # Drawing material is separate from the fixed placement/movement role.
            specs=[('pot','植木鉢',MaterialClass.STANDARD_PROP),('foliage','葉と枝',MaterialClass.STANDARD_PROP)]
        for key,label,kind in specs:
            if key in registered:continue
            obj.components.append(ObjectComponent(component_id=key,label=label,material_class=kind,
                reference_ids=list(obj.reference_ids),rendering='soft_fabric' if kind in {MaterialClass.FABRIC_FURNITURE,MaterialClass.FABRIC_PROP} else 'rigid'))
    scene.schema_version=3
    return scene

def display_components(obj):
    # Old data can be displayed without changing the authoritative revision.
    return obj.components or [ObjectComponent(component_id='body',label=obj.label,material_class=inferred_material(obj),reference_ids=obj.reference_ids)]

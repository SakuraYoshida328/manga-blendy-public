"""Per-image manga settings. They never alter SceneRevision or SceneState."""
from typing import Literal
from pydantic import Field, model_validator
from .domain import Model

class MangaRenderOptions(Model):
    focus_mode: Literal['character','balanced','background']='character'
    character_outline_px: float=Field(default=1.6,ge=.25,le=16)
    detail_level: Literal['low','medium','high']|None=None
    resolution: tuple[int,int]=(1448,1086)

    @model_validator(mode='after')
    def valid_resolution(self):
        if min(self.resolution)<64 or max(self.resolution)>4096:
            raise ValueError('output size must be 64..4096 pixels')
        return self

    def resolved(self):
        level=self.detail_level or {'character':'low','balanced':'medium','background':'high'}[self.focus_mode]
        ratio={'character':.6,'balanced':.8,'background':1.0}[self.focus_mode]
        main=self.character_outline_px*ratio
        return {'focus_mode':self.focus_mode,'character_outline_px':self.character_outline_px,
                'main_line_px':main,'fabric_line_px':main*.65,'detail_line_px':main*.42,
                'detail_level':level,'detail_strength':{'low':.22,'medium':.52,'high':.85}[level],
                'tone_strength':{'character':.30,'balanced':.60,'background':.85}[self.focus_mode],
                'resolution':list(self.resolution),'structural_elements_preserved':True}

class BackgroundRenderRequest(Model):
    stage: Literal['production_drawing','reference_validation']='production_drawing'
    camera_id: str=Field(min_length=1,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    render: MangaRenderOptions=Field(default_factory=MangaRenderOptions)
    # A per-output trial, not a permanent model/default-workflow change.
    workflow: Literal['one_stage','two_stage']='one_stage'

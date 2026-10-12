"""User-approved improvement records. Approval never executes or edits source code."""
from .domain import Model,DomainError,uid,now
from .storage import atomic_json,read_json,safe_path
from pydantic import Field

class ImprovementProposal(Model):
    description: str=Field(min_length=1,max_length=4000)
    scope: str=Field(default='background_quality',min_length=1,max_length=200)

def propose(store,pid,body):
    data=ImprovementProposal.model_validate(body);manifest=store.manifest(pid)
    record={'proposal_id':uid('improvement'),'description':data.description,'scope':data.scope,
        'status':'proposed','base_revision_id':manifest.active_revision_id,'base_state_id':manifest.active_state_id,
        'created_at':now(),'source_code_modified':False,'implementation_commit':None}
    atomic_json(store.project(pid)/'improvements'/(record['proposal_id']+'.json'),record)
    return record

def approve(store,pid,proposal_id,expected_revision):
    if not __import__('re').fullmatch(r'improvement_[a-f0-9]{16}',proposal_id):
        raise DomainError('INPUT_INVALID','改善提案IDが不正です。')
    path=safe_path(store.project(pid),f'improvements/{proposal_id}.json')
    record=read_json(path);manifest=store.manifest(pid)
    if record['base_revision_id']!=expected_revision or manifest.active_revision_id!=expected_revision:
        raise DomainError('REVISION_CONFLICT','提案時のマスターから変更されています。内容を再確認してください。')
    record.update(status='approved_for_developer_review',approved_at=now(),source_code_modified=False)
    atomic_json(path,record)
    return record

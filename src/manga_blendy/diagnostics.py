"""Read-only local onboarding diagnostics. No install, authentication or inference."""
from .blender_adapter import diagnose
from .mcp_transport import addon_command
from .domain import DomainError,Model
from typing import Literal
from pydantic import Field

class SetupOptions(Model):
    operation_mode: Literal['chatgpt_plan']='chatgpt_plan'
    blender_path: str=Field(min_length=1,max_length=2048)

def capabilities():
    return {'schema_version':1,'operation_modes':['chatgpt_plan'],
        'paid_api_enabled':False,'automatic_billing_fallback':False,
        'ai_analysis':{'backend':'observation_json_import','automatic_inference':False},
        'manga_finish':{'backend':'external_png','automatic_generation':False,
            'workflow_options':['one_stage','two_stage'],'human_review_required':True},
        'blender':{'required_version':'5.2','saved_scene_renderer':'existing_blender_mcp'},
        'plan_api_image_generation':False,
        'official_limitations':'https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations'}

def check_environment(path):
    report={'blender':{'ready':False},'mcp':{'ready':False},'capabilities':capabilities()}
    try:report['blender']={'ready':True,'version':diagnose(path)}
    except DomainError as exc:report['blender']={'ready':False,'code':exc.code,'message':str(exc)}
    try:
        result=addon_command('get_scene_info',timeout=5)
        report['mcp']={'ready':True,'host':'127.0.0.1','port':9876,
            'message':'既存MCPサーバーへ読み取り要求が成功しました。シーンを変更していません。'}
    except DomainError as exc:
        report['mcp']={'ready':False,'code':exc.code,
            'message':'BlenderのMCPアドオンで接続サーバーを起動してください。127.0.0.1:9876を使用します。'}
    return report

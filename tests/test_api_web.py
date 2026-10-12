import json

import httpx
import pytest
from fastapi.testclient import TestClient

from manga_blendy.analysis import OpenAIProvider, ObservationProposal, strict_schema
from manga_blendy.domain import DomainError
from manga_blendy.web.app import create_app
from test_domain_storage import prepare, source_bytes


@pytest.mark.parametrize("payload,code",[
    ({"status":"incomplete","output":[]},"AI_INCOMPLETE"),
    ({"status":"completed","output":[{"content":[{"type":"refusal","refusal":"no"}]}]},"AI_REFUSAL"),
    ({"status":"completed","output":[{"content":[{"type":"output_text","text":"{}"}]}]},"AI_OUTPUT_INVALID")])
def test_api_failure_is_not_fake_success(tmp_path,monkeypatch,payload,code):
    monkeypatch.setenv("OPENAI_API_KEY","test-only-key")
    image=tmp_path/"test.png";image.write_bytes(source_bytes())
    calls=[]
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json=payload)
    provider=OpenAIProvider("test-model",transport=httpx.MockTransport(respond))
    with pytest.raises(DomainError) as exc:provider.observe(image,{},lambda *a:None)
    assert exc.value.code==code
    assert len(calls)==(2 if code=="AI_OUTPUT_INVALID" else 1)
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["strict"] is True


def test_timeout_is_not_retried(tmp_path,monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","test-only-key")
    image=tmp_path/"test.png";image.write_bytes(source_bytes());calls=[]
    def respond(request):
        calls.append(1)
        raise httpx.ReadTimeout("timeout")
    with pytest.raises(DomainError) as exc:OpenAIProvider("test-model",transport=httpx.MockTransport(respond)).observe(image,{},lambda *a:None)
    assert exc.value.code=="AI_TIMEOUT" and len(calls)==1


def test_structured_schema_uses_supported_array_and_strict_objects():
    schema=strict_schema(ObservationProposal)
    assert "prefixItems" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"])==set(schema["properties"])


def test_valid_observation_response_contract(tmp_path,monkeypatch):
    from test_planning_jobs import proposal_for_fixture
    expected=proposal_for_fixture()
    monkeypatch.setenv('OPENAI_API_KEY','test-only-key')
    image=tmp_path/'test.png';image.write_bytes(source_bytes())
    payload={'status':'completed','output':[{'content':[{'type':'output_text','text':expected.model_dump_json()}]}]}
    provider=OpenAIProvider('test-model',transport=httpx.MockTransport(lambda request:httpx.Response(200,json=payload)))
    observed=provider.observe(image,{},lambda *args:None)
    assert observed==expected
    assert observed.candidates[0].opening.rows==1


def test_saved_packet_opens_without_api_and_mutations_require_token(tmp_path,monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    store,manifest,scene,state=prepare(tmp_path)
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app) as client:
        result=client.get(f"/projects/{manifest.project_id}")
        assert result.status_code==200 and "日本語の部屋" in result.text
        import re
        token=re.search('name="manga-token" content="([^"]+)"',result.text).group(1)
        body={"parent_state_id":state.state_id,"expected_state_id":state.state_id,"story_time":"翌日","commands":[]}
        endpoint=f"/api/projects/{manifest.project_id}/states"
        assert client.post(endpoint,json=body).status_code==400
        assert client.post(endpoint,json=body,headers={"x-manga-token":token,"origin":"https://foreign.example"}).status_code==400
        response=client.post(endpoint,json=body,headers={"x-manga-token":token})
        assert response.status_code==200
        later=store.state(manifest.project_id,response.json()["state_id"])
        assert later.objects==state.objects
        assert client.get(f"/projects/{manifest.project_id}/file/../config.json").status_code != 200

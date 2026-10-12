"""Import Codex/ChatGPT observations without making any AI or billable API call."""
import hashlib

from .analysis import ObservationProposal
from .domain import DomainError, Model
from pydantic import Field
from typing import Literal

MAX_OBSERVATION_BYTES = 2*1024*1024


class ObservationPackage(Model):
    contract: Literal["manga-blendy.observation"] = "manga-blendy.observation"
    schema_version: Literal[1] = 1
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation: ObservationProposal


def import_observation(image_bytes, json_bytes):
    if len(json_bytes) > MAX_OBSERVATION_BYTES:
        raise DomainError("INPUT_INVALID", "解析JSONは2MB以下にしてください。")
    package = ObservationPackage.model_validate_json(json_bytes)
    if package.source_sha256 != hashlib.sha256(image_bytes).hexdigest():
        raise DomainError("SOURCE_MISMATCH", "解析JSONの元画像ハッシュと、選択した画像が一致しません。")
    return package.observation

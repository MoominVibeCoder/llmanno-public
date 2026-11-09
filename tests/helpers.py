import json
from pathlib import Path
from typing import Any

import openpyxl

from llmanno.core.engine import Engine
from llmanno.core.providers.base import ChatResponse, ModelInfo


def write_xlsx(path: Path, header: list[str], rows: list[list[Any]], sheet: str = "Daten") -> Path:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(header)
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    return path


TEXTS = ["Was ist ein Bruch?", "Ein Bruch ist ein Teil.", "Bruch heißt Teil."]


def ans(code="K1", evidence=("Bruch",), confidence=90, alternatives=()):
    candidates = [{"evidence": list(evidence), "code": code, "confidence": confidence}]
    candidates += [{"evidence": ["Teil"], "code": c, "confidence": 5} for c in alternatives]
    return json.dumps({"rationale": f"weil {code}", "candidates": candidates})


class FakeProvider:
    def __init__(self, script, *, digest="d1", context_length=32768, tokens_in=100):
        self.script = script
        self.requests = []
        self.digest = digest
        self.context_length = context_length
        self.tokens_in = tokens_in

    def chat(self, request):
        self.requests.append(request)
        out = self.script(request, len(self.requests))
        if isinstance(out, Exception):
            raise out
        return ChatResponse(
            text=out, model="fake", tokens_in=self.tokens_in, tokens_out=20, latency_s=0.5,
            output_mode="json_schema", finish_reason="stop",
        )  # fmt: skip

    def list_models(self):
        return [ModelInfo("fake", digest=self.digest, context_length=self.context_length)]

    def close(self):
        pass


def engine(project, provider, sleeps=None):
    return Engine(
        project,
        provider_factory=lambda cfg: provider,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )

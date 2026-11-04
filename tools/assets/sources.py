"""에셋 도구의 입출력(인프라): 규격표·콘텐츠 묶음·캠페인 읽기, 파일·manifest 쓰기, 구조화 로그, 결과 JSON."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from tools.assets.spec import KINDS, AssetEntry, AssetSpec, SpecError, parse_spec_md, sort_key

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "docs" / "assets" / "spec.md"
BUNDLE_PATH = ROOT / "fixtures" / "content_min" / "bundle.json"
CAMPAIGNS_PATH = ROOT / "fixtures" / "ads" / "campaigns.json"


def load_spec(path: Path = SPEC_PATH) -> AssetSpec:
    """규격표(`docs/assets/spec.md`)를 읽는다."""
    return parse_spec_md(path.read_text(encoding="utf-8"))


def load_npc_ids(path: Path = BUNDLE_PATH) -> list[str]:
    """콘텐츠 묶음의 인물 ID(`characters[].npc_id`)."""
    bundle = json.loads(path.read_text(encoding="utf-8"))
    return [c["npc_id"] for c in bundle["characters"]]


def load_place_ids() -> list[str]:
    """계약 `Place` 값 5개."""
    from engine.domain.model import Place
    return [p.value for p in Place]


def load_campaigns(path: Path = CAMPAIGNS_PATH) -> tuple[list[dict], dict[str, tuple[int, int]]]:
    """캠페인 목록과 지면 규격. 픽스처의 `slot_sizes` 사본이 계약 `SLOT_SIZES`와 다르면 SpecError."""
    from ads.domain.model import SLOT_SIZES
    data = json.loads(path.read_text(encoding="utf-8"))
    contract = {slot.value: tuple(size) for slot, size in SLOT_SIZES.items()}
    copy = {k: tuple(v) for k, v in data.get("slot_sizes", {}).items()}
    if copy != contract:
        raise SpecError(f"campaigns.json slot_sizes {copy} ≠ 계약 SLOT_SIZES {contract}. 픽스처를 다시 만든다")
    return data["campaigns"], contract


def slot_image_path(image_path: str, slot: str) -> str:
    """계약 함수 `ads.domain.model.slot_image_path`를 그대로 부른다(파일명 규칙의 원본)."""
    from ads.domain.model import slot_image_path as contract_fn
    return contract_fn(image_path, slot)


def load_manifest(repo: Path, spec: AssetSpec) -> list[AssetEntry]:
    """기존 manifest. 없으면 빈 목록."""
    path = repo / spec.manifest_path
    if not path.exists():
        return []
    out = []
    for d in json.loads(path.read_text(encoding="utf-8")):
        size = tuple(d["size"]) if d.get("size") is not None else None
        w, h = size if size else spec.sizes.get(d["kind"], (0, 0))
        out.append(AssetEntry(kind=d["kind"], id=d["id"], path=d["path"], url=d["url"],
                              width=w, height=h, size=size, placeholder=bool(d["placeholder"])))
    return out


def write_manifest(repo: Path, spec: AssetSpec, entries: Iterable[AssetEntry]) -> Path:
    """manifest.json을 정렬해 쓴다(임시 파일 → 교체)."""
    path = repo / spec.manifest_path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps([e.to_json() for e in sorted(entries, key=sort_key)], ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(body, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def write_file(repo: Path, rel_path: str, data: bytes) -> None:
    """저장소 기준 경로에 바이트를 쓴다."""
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def list_asset_files(repo: Path, spec: AssetSpec) -> list[str]:
    """`<root>/<kind>/` 아래 파일(저장소 기준 경로, `/` 구분)."""
    out = []
    for kind in KINDS:
        d = repo / spec.root / kind
        if d.is_dir():
            out += [f"{spec.root}/{kind}/{p.name}" for p in sorted(d.iterdir()) if p.is_file()]
    return out


class JsonLogger:
    """README 5절 구조화 로그: JSON 한 줄을 표준 출력과 `out/logs/<service>.jsonl`에."""

    def __init__(self, repo: Path, service: str = "assets", component: str = "make_placeholders",
                 stream=None):
        self.service, self.component = service, component
        self.stream = stream if stream is not None else sys.stdout
        self.path = repo / "out" / "logs" / f"{service}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, level: str, msg: str, **fields) -> None:
        """한 줄 기록. 오류면 `error_type`을 넣는다."""
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
               "level": level, "service": self.service, "component": self.component, "msg": msg, **fields}
        line = json.dumps(rec, ensure_ascii=False)
        print(line, file=self.stream)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _git_head(repo: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def write_result(repo: Path, task: str, name: str, metrics: dict, source: str) -> Path:
    """README 4절 결과 JSON. `tools.evidence_result.write_result`(E-05-02)가 있으면 그것을 쓰고, 없으면 같은 형식으로 쓴다."""
    try:
        from tools.evidence_result import write_result as shared  # type: ignore
    except ImportError:
        shared = None
    if shared is not None and repo.resolve() == ROOT:
        return Path(shared(task, name, metrics, source))
    path = repo / "docs" / "evidence" / "perf" / task / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"task": task, "commit": _git_head(repo),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": source, "metrics": metrics}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path

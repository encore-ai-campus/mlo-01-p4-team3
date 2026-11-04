"""Python 환경 확인(C-05-03 조건 2): 패키지마다 import·간단한 동작·버전을 구조화 로그로 출력한다.

사용법(확인할 venv의 python으로):
    .venv\\Scripts\\python -m ops.compose.pycheck                 # 필수 5개
    .venv\\Scripts\\python -m ops.compose.pycheck --survey        # 조사 항목까지
하나라도 실패하면 종료 코드 1. 표준 라이브러리만 import하고, 대상 패키지는 실행 중에 불러온다.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import platform
import sys
import time

from ops.compose import devenv


def _torch(m):
    return f"sum={float(m.ones(2, 3).sum())} threads={m.get_num_threads()}"


def _transformers(m):
    from transformers import BertConfig, BertModel  # 모델 다운로드 없이 작은 무작위 모델로 순전파
    import torch
    cfg = BertConfig(vocab_size=100, hidden_size=32, num_hidden_layers=1, num_attention_heads=2, intermediate_size=64)
    out = BertModel(cfg)(torch.tensor([[1, 2, 3]])).last_hidden_state
    return f"bert_forward_shape={tuple(out.shape)}"


def _lightgbm(m):
    import numpy as np
    x = np.arange(200, dtype=float).reshape(100, 2)
    y = (x[:, 0] > 100).astype(int)
    params = {"objective": "binary", "verbose": -1, "min_data_in_leaf": 5}
    booster = m.train(params, m.Dataset(x, y), num_boost_round=5)   # 기본 API(scikit-learn 불필요)
    return f"train_acc={float(((booster.predict(x) > 0.5) == y).mean()):.2f}"


def _llama_cpp(m):
    return "system_info=" + m.llama_print_system_info().decode()[:80]


REQUIRED = [("torch", "torch", _torch), ("transformers", "transformers", _transformers),
            ("lightgbm", "lightgbm", _lightgbm), ("pyspark", "pyspark", None),
            ("llama-cpp-python", "llama_cpp", _llama_cpp)]
SURVEY = [("scikit-learn", "sklearn", None), ("shap", "shap", None), ("delta-spark", "delta", None),
          ("confluent-kafka", "confluent_kafka", None), ("pymongo", "pymongo", None), ("PyMySQL", "pymysql", None),
          ("Django", "django", None), ("langgraph", "langgraph", None), ("mlflow", "mlflow", None),
          ("prometheus-client", "prometheus_client", None)]


def check(dist: str, module: str, smoke) -> tuple[bool, dict]:
    """패키지 하나를 import하고(있으면) 간단한 동작을 실행한다."""
    start = time.monotonic()
    fields = {"package": dist, "module": module}
    try:
        m = importlib.import_module(module)
        fields["version"] = md.version(dist)
        if smoke:
            fields["smoke"] = smoke(m)
        ok = True
    except Exception as e:  # 실패도 결과다. 기록하고 다음 패키지로 간다
        fields.update(error_type=type(e).__name__, error=str(e)[:200])
        ok = False
    fields["duration_ms"] = int((time.monotonic() - start) * 1000)
    return ok, fields


def main(argv=None) -> int:
    """명령줄 진입점."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--survey", action="store_true", help="조사 항목 패키지도 확인")
    args = ap.parse_args(argv)
    env = {"python": platform.python_version(), "executable": sys.executable, "base_prefix": sys.base_prefix,
           "os": platform.platform()}
    print(devenv.log_record("info", "pycheck", "환경", **env), flush=True)
    failed = 0
    for group, items in (("required", REQUIRED), ("survey", SURVEY if args.survey else [])):
        for dist, module, smoke in items:
            ok, fields = check(dist, module, smoke)
            failed += (not ok) and group == "required"
            print(devenv.log_record("info" if ok else "error", "pycheck", f"{dist} {'OK' if ok else 'FAIL'}",
                                    group=group, **fields), flush=True)
    print(devenv.log_record("info" if not failed else "error", "pycheck", f"필수 실패 {failed}건"), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

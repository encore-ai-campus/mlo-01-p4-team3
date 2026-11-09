"""추정 결과·예외를 바꾸지 않고 모델 포트의 지연·버전·실패를 계측한다."""
from time import perf_counter
from prometheus_client import Counter,Gauge,Histogram
from engine.ports import FeatureVersionMismatch,ModelUnavailable

DURATION=Histogram('paldo_model_inference_duration_seconds','Suspicion model inference duration',['model_version','result'],buckets=(.001,.005,.01,.025,.05,.1,.25,.5,1))
CALLS=Counter('paldo_model_calls_total','Suspicion model port calls',['model_version','result'])
VERSIONS=Gauge('paldo_model_info','Configured suspicion model version',['model_version','feature_version','tier'])
RUNTIME_LLM=Gauge('paldo_runtime_llm_enabled','Whether the game uses a runtime LLM')
DECISIONS=Counter('paldo_model_decisions_total','Actual engine model decisions',['model_version','canary','result'])


def observe_decisions(transition,model_version):
    """실제 엔진 이유 코드로 폴백을 세고 판 식별자·대사·진실은 지표에 넣지 않는다."""
    canary=str(bool(transition.state and transition.state.meta.canary)).lower()
    for event in transition.events:
        if event.event_type.value!='decision':continue
        codes=event.payload.get('reason_codes',[])
        if any(str(code).startswith('model_fallback_') for code in codes):result='fallback'
        elif 'model_estimate' in codes:result='ok'
        else:continue
        DECISIONS.labels(model_version,canary,result).inc()


class ObservedSuspicionModel:
    """기준선도 그 실제 버전으로 표시한다. 실패를 성공이나 실제 학습 모델로 숨기지 않는다."""
    def __init__(self,model):
        """모델의 식별자와 추정 포트를 그대로 보존한다."""
        self.model=model;self.feature_version=model.feature_version
        self.model_version=getattr(model,'model_version','unknown')
        VERSIONS.labels(self.model_version,self.feature_version,'baseline' if self.model_version.startswith('fake-') else 'model').set(1)
        RUNTIME_LLM.set(0)

    def estimate(self,obs):
        """호출 시간을 관찰하되 반환값과 실패 전파를 원래 포트와 같게 유지한다."""
        started=perf_counter();result='error'
        try:
            value=self.model.estimate(obs);result='ok';return value
        except (ModelUnavailable,FeatureVersionMismatch):
            result='fallback';raise
        finally:
            DURATION.labels(self.model_version,result).observe(perf_counter()-started)
            CALLS.labels(self.model_version,result).inc()

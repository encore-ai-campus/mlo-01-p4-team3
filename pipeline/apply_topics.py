"""C-06-02: 선언한 새 토픽만 생성하며 기존 토픽 변경·삭제는 하지 않는다."""
import argparse
import json
from pathlib import Path
from tools.jsonlog import log


def load_plan(path):
    """JSON 문법의 YAML을 읽어 키·복제·보존 정의를 확인한다."""
    plan=json.loads(Path(path).read_text(encoding='utf-8'));defaults=plan['defaults']
    topics=plan['topics'];names=[t['name'] for t in topics]
    if len(names)!=len(set(names)) or defaults['partitions']<1 or defaults['replication_factor']!=3:
        raise ValueError('invalid_topic_plan')
    if defaults['min_insync_replicas']!=2 or defaults['retention_ms']!=86400000:raise ValueError('topic_contract_mismatch')
    return plan


def apply(plan,client):
    """없던 토픽만 생성하고 최종 partition/replica 구성을 확인한다."""
    from confluent_kafka.admin import NewTopic,ConfigResource,ResourceType
    defaults=plan['defaults'];metadata=client.list_topics(timeout=15)
    new=[NewTopic(t['name'],num_partitions=defaults['partitions'],replication_factor=defaults['replication_factor'],
        config={'retention.ms':str(defaults['retention_ms']),'min.insync.replicas':str(defaults['min_insync_replicas'])})
        for t in plan['topics'] if t['name'] not in metadata.topics]
    for _,future in client.create_topics(new).items() if new else []:future.result(30)
    result=client.list_topics(timeout=15)
    for topic in plan['topics']:
        data=result.topics[topic['name']]
        if data.error or len(data.partitions)!=defaults['partitions'] or any(len(p.replicas)!=3 for p in data.partitions.values()):
            raise RuntimeError('existing_topic_topology_mismatch')
    resources=[ConfigResource(ResourceType.TOPIC,t['name']) for t in plan['topics']]
    for _,future in client.describe_configs(resources).items():
        config=future.result(30)
        if config['retention.ms'].value!=str(defaults['retention_ms']) or config['min.insync.replicas'].value!=str(defaults['min_insync_replicas']):
            raise RuntimeError('existing_topic_config_mismatch')
    return {'topic_count':len(plan['topics']),'created':len(new)}


def main(argv=None):
    """기본은 계획만 출력한다. --apply일 때만 새 토픽을 생성한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--plan',default='pipeline/topics.yaml')
    parser.add_argument('--client-config',type=Path);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    try:
        plan=load_plan(args.plan)
        if not args.apply:log('apply_topics','info','topic_plan_validated',topic_count=len(plan['topics']));return
        if not args.client_config:parser.error('--apply requires --client-config')
        from confluent_kafka.admin import AdminClient
        config=json.loads(args.client_config.read_text())
        if config.get('security.protocol')!='SSL' or config.get('ssl.endpoint.identification.algorithm','https')!='https':
            raise ValueError('verified_tls_required')
        log('apply_topics','info','topics_ready',**apply(plan,AdminClient(config)))
    except Exception as error:
        log('apply_topics','error','topic_apply_failed',error_type=type(error).__name__)
        raise SystemExit(1) from None


if __name__=='__main__':main()

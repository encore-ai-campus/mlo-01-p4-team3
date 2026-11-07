"""B-07-02용 독립 Kafka 4.3.2 상호 TLS 시험 호스트. 비밀은 out/handoff에만 쓴다."""
from datetime import datetime, timedelta, timezone
import base64
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from confluent_kafka.admin import AdminClient



def main():
    """Docker Desktop/Linux Docker에 고유 컨테이너·인증서·루프백 포트를 준비한다."""
    root = (Path('out/handoff') / ('kafka-b0702-' + uuid.uuid4().hex[:8])).resolve()
    root.mkdir(parents=True, exist_ok=False)
    docker = shutil.which('docker') or str(Path(os.environ['LOCALAPPDATA']) / 'Programs/DockerDesktop/resources/bin/docker.exe')
    image = 'apache/kafka:4.3.2'
    name = 'paldo-b0702-' + uuid.uuid4().hex[:8]
    password = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'B-07-02 ephemeral test CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
          .not_valid_after(now+timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),False)
            .sign(ca_key,hashes.SHA256()))
    (root/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (root/'client.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (root/'client.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    (root/'server.p12').write_bytes(pkcs12.serialize_key_and_certificates(b'broker',key,cert,[ca],serialization.BestAvailableEncryption(password.encode())))
    mount = 'type=bind,source='+str(root)+',target=/mnt/tls'
    subprocess.run([docker,'run','--rm','--mount',mount,'--entrypoint','keytool',image,'-importcert','-noprompt',
        '-alias','localCA','-file','/mnt/tls/ca.pem','-keystore','/mnt/tls/trust.p12','-storetype','PKCS12','-storepass',password],
        check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); port = listener.getsockname()[1]
    properties = f'''process.roles=broker,controller
    node.id=1
    controller.quorum.voters=1@localhost:9093
    listeners=HOST://:9092,INTERNAL://:9094,CONTROLLER://:9093
    advertised.listeners=HOST://localhost:{port},INTERNAL://localhost:9094
    listener.security.protocol.map=HOST:SSL,INTERNAL:SSL,CONTROLLER:PLAINTEXT
    controller.listener.names=CONTROLLER
    inter.broker.listener.name=INTERNAL
    ssl.keystore.location=/mnt/tls/server.p12
    ssl.keystore.type=PKCS12
    ssl.keystore.password={password}
    ssl.key.password={password}
    ssl.truststore.location=/mnt/tls/trust.p12
    ssl.truststore.type=PKCS12
    ssl.truststore.password={password}
    ssl.client.auth=required
    ssl.endpoint.identification.algorithm=https
    log.dirs=/tmp/kraft-data
    num.partitions=1
    offsets.topic.replication.factor=1
    transaction.state.log.replication.factor=1
    transaction.state.log.min.isr=1
    min.insync.replicas=1
    group.initial.rebalance.delay.ms=0
    auto.create.topics.enable=false
    '''
    (root/'server.properties').write_text(properties,encoding='utf-8')
    cluster = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip('=')
    start = '/opt/kafka/bin/kafka-storage.sh format -t '+cluster+' -c /mnt/tls/server.properties && exec /opt/kafka/bin/kafka-server-start.sh /mnt/tls/server.properties'
    subprocess.run([docker,'run','-d','--name',name,'--label','paldo.test=B-07-02','--memory','1g','--mount',mount,'-p',f'127.0.0.1:{port}:9092',
                   '-e','KAFKA_HEAP_OPTS=-Xms256m -Xmx384m','--entrypoint','/bin/bash',image,'-c',start],
                   check=True,stdout=subprocess.DEVNULL,timeout=30)
    config = {'bootstrap.servers':f'localhost:{port}','security.protocol':'SSL','broker.address.family':'v4',
              'ssl.ca.location':str(root/'ca.pem'),'ssl.certificate.location':str(root/'client.pem'),
              'ssl.key.location':str(root/'client.key'),'ssl.endpoint.identification.algorithm':'https',
              'enable.ssl.certificate.verification':True,'log_level':0}
    (root/'client.json').write_text(json.dumps(config),encoding='utf-8')
    (root/'resource.json').write_text(json.dumps({'container':name,'port':port}),encoding='utf-8')
    deadline = time.monotonic()+90
    while True:
        try:
            metadata=AdminClient(config).list_topics(timeout=2)
            assert metadata.brokers
            print('Kafka 4.3.2 ready: mutual TLS, hostname and CA verified',flush=True)
            print('PALDO_TEST_KAFKA_CONFIG=' + str(root / 'client.json'),flush=True)
            print('test_container=' + name,flush=True)
            break
        except Exception:
            if time.monotonic()>=deadline:
                with (root/'startup.log').open('wb') as output:
                    subprocess.run([docker,'logs',name],stdout=output,stderr=subprocess.STDOUT)
                raise RuntimeError('kafka_startup_failed') from None
            time.sleep(1)


if __name__ == "__main__":
    main()

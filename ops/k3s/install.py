"""검증한 공식 바이너리·airgap 이미지만 써 기존 Spark 옆에 k3s를 설치한다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def install(profile):
    """기존 비관리 설치를 거절하고 root 전용 설정·토큰 참조와 전용 unit을 만든다."""
    manifest=json.loads(Path(profile['manifest']).read_text());version=manifest['version']
    if not re.fullmatch(r'v\d+\.\d+\.\d+\+k3s\d+',version):raise ValueError('stable_version_required')
    role=profile['role']
    if role not in ('server','agent'):raise ValueError('invalid_role')
    root=Path('/opt/pj4/k3s')/version;receipt=root/'receipt.json'
    if (shutil.which('k3s') or Path('/etc/rancher/k3s/config.yaml').exists()) and not receipt.exists():raise ValueError('unmanaged_k3s_refused')
    for key,name in [('binary','k3s'),('images','k3s-airgap-images-amd64.tar.zst')]:
        with Path(profile[key]).open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=manifest['artifacts'][name]['sha256']:raise ValueError('upstream_hash_mismatch')
    root.mkdir(parents=True,exist_ok=True)
    binary=root/'k3s'
    if not binary.exists():shutil.copyfile(profile['binary'],binary)
    elif hashlib.sha256(binary.read_bytes()).hexdigest()!=manifest['artifacts']['k3s']['sha256']:raise ValueError('managed_binary_changed')
    binary.chmod(0o755)
    state=Path('/var/lib/pj4-k3s');state.mkdir(mode=0o700,exist_ok=True)
    images=state/'agent/images';images.mkdir(parents=True,exist_ok=True)
    image=images/'k3s-airgap-images-amd64.tar.zst'
    if not image.exists():shutil.copyfile(profile['images'],image)
    private=Path('/etc/pj4/k3s');private.mkdir(parents=True,mode=0o700,exist_ok=True);private.chmod(0o700)
    config={'data-dir':str(state),'node-ip':profile['node_ip'],'node-name':profile['node_name'],
        'prefer-bundled-bin':True,'node-label':['paldo-role=simulation'],
        'kubelet-arg':['address='+profile['node_ip'],'system-reserved=cpu=250m,memory=384Mi','kube-reserved=cpu=250m,memory=256Mi']}
    if role=='server':
        config.update({'bind-address':profile['node_ip'],'advertise-address':profile['node_ip'],
            'tls-san':[profile['node_ip']],'secrets-encryption':True,'write-kubeconfig':str(private/'admin.yaml'),
            'write-kubeconfig-mode':'0600','disable':['traefik','servicelb','metrics-server'],
            'kube-apiserver-arg':['anonymous-auth=false']})
    else:
        token=Path(profile['token_file'])
        if token.stat().st_mode & 0o077:raise ValueError('private_token_required')
        config.update({'server':profile['server_url'],'token-file':str(token)})
    destination=private/(role+'.json')
    if destination.exists() and json.loads(destination.read_text())!=config:raise ValueError('existing_config_changed')
    destination.write_text(json.dumps(config)+'\n');destination.chmod(0o600)
    for module in ('overlay','br_netfilter'):subprocess.run(['modprobe',module],check=True,capture_output=True)
    sysctl=Path('/etc/sysctl.d/90-paldo-k3s.conf')
    sysctl.write_text('net.ipv4.ip_forward=1\nnet.bridge.bridge-nf-call-iptables=1\n')
    subprocess.run(['sysctl','-p',str(sysctl)],check=True,capture_output=True)
    unit=f'''[Unit]
Description=Paldo k3s {role}
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
ExecStart={binary} {role} --config {destination}
Restart=on-failure
RestartSec=10
TimeoutStartSec=300
TimeoutStopSec=90
Delegate=yes
TasksMax=infinity
LimitNOFILE=1048576
MemoryMax={'1600M' if role=='server' else '1000M'}
[Install]
WantedBy=multi-user.target
'''
    Path('/etc/systemd/system/paldo-k3s.service').write_text(unit)
    receipt.write_text(json.dumps(manifest)+'\n')
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','paldo-k3s'],check=True,capture_output=True)
    return {'role':role,'version':version,'hash_verified':True,'existing_spark_unchanged':True}


def main(argv=None):
    """값은 외부 프로필에서 읽고 합류 토큰·kubeconfig를 출력하지 않는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--profile',type=Path,required=True);args=parser.parse_args(argv)
    if os.geteuid()!=0:parser.error('administrator required')
    print(json.dumps(install(json.loads(args.profile.read_text()))))


if __name__=='__main__':main()

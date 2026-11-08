"""VM의 검증된 런처를 지속 실행하며 기존 checkpoint와 단일 writer lock을 쓴다."""
import argparse
from pathlib import Path
import pwd
import subprocess


def main(argv=None):
    """공식 bronze 작업만 등록한다. 다른 프로젝트 서비스는 변경하지 않는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--launcher',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True);parser.add_argument('--user',required=True)
    args=parser.parse_args(argv);launcher=args.launcher.resolve(strict=True);state=args.state.resolve(strict=True)
    identity=pwd.getpwnam(args.user)
    if any(c.isspace() for c in str(launcher)+str(state)) or identity.pw_uid==0:raise ValueError('service_identity_or_path')
    unit=f'''[Unit]
Description=Paldo official Kafka to Delta bronze
After=network-online.target
Wants=network-online.target
[Service]
Type=exec
User={identity.pw_name}
Group={identity.pw_name}
WorkingDirectory={launcher.parent}
ExecStart={launcher} bronze
Restart=on-failure
RestartSec=15
KillMode=control-group
TimeoutStopSec=90
UMask=0027
MemoryMax=2300M
NoNewPrivileges=yes
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
'''
    Path('/etc/systemd/system/paldo-bronze.service').write_text(unit)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','paldo-bronze'],check=True,capture_output=True)
    print('paldo_bronze_enabled=1')


if __name__=='__main__':main()

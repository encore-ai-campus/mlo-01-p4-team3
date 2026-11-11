"""보호 입력을 사용하는 재학습 Job의 SSH 추적 연결과 영속 실행 폴더를 조립한다."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from uuid import uuid4

from ops.mlflow.retrain import run, validate


@contextmanager
def tracking_tunnel(config):
    """허용한 추적 서버만 SSH로 연결하고 작업 종료 시 전용 자식을 회수한다."""
    tunnel = config.get('mlflow_tunnel')
    if not tunnel:
        yield
        return
    if not re.fullmatch(r'(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9][A-Za-z0-9.-]*', tunnel['target']):
        raise ValueError('invalid_tracking_target')
    with tempfile.TemporaryDirectory(prefix='paldo-mlflow-') as directory:
        key = Path(directory) / 'identity'
        shutil.copyfile(tunnel['identity'], key)
        key.chmod(0o600)
        local_port, remote_port = int(tunnel['local_port']), int(tunnel['remote_port'])
        if not 1024 <= local_port <= 65535 or not 1 <= remote_port <= 65535:
            raise ValueError('invalid_tracking_port')
        args = ['ssh', '-N', '-T', '-i', str(key), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + tunnel['known_hosts'],
                '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
                '-L', f'127.0.0.1:{local_port}:127.0.0.1:{remote_port}', tunnel['target']]
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        uri = f'http://127.0.0.1:{local_port}'
        previous = os.environ.get('MLFLOW_TRACKING_URI')
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError('tracking_tunnel_failed')
                try:
                    with urllib.request.urlopen(uri + '/health', timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('tracking_tunnel_health_timeout')
                time.sleep(.2)
            os.environ['MLFLOW_TRACKING_URI'] = uri
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if previous is None:
                os.environ.pop('MLFLOW_TRACKING_URI', None)
            else:
                os.environ['MLFLOW_TRACKING_URI'] = previous


def execution_directory(root):
    """같은 PVC의 다음 야간 실행이 이전 로그·산출물을 덮어쓰지 않게 한다."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return Path(root) / (stamp + '-' + uuid4().hex[:12])


def main(argv=None):
    """초기 수동 실행과 예약 실행이 같은 검증·등록 실행기를 사용한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    if args.config.is_symlink() or (os.name != 'nt' and args.config.stat().st_mode & 0o007):
        parser.error('protected_retrain_config_required')
    config = json.loads(args.config.read_text(encoding='utf-8'))
    validate(config)
    if args.check:
        print(json.dumps({'configuration_valid': True, 'families': list(config['models'])}))
        return
    os.umask(0o077)
    args.out.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.out).free < config.get('minimum_free_bytes', 2 * 1024 ** 3):
        raise RuntimeError('insufficient_training_output_space')
    output = execution_directory(args.out)
    with tracking_tunnel(config):
        result = run(config, output)
    print(json.dumps({'families_completed': len(result), 'accepted': sum(r['accepted'] for r in result)}))


if __name__ == '__main__':
    main()

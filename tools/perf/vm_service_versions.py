"""실행 중 VM의 설치 패키지 버전과 active unit을 읽기 전용으로 출력한다."""
import json,os,re,subprocess
from pathlib import Path
def run(args):
    """제한 시간 안에 성공한 명령의 표준 출력만 반환한다."""
    p=subprocess.run(args,capture_output=True,text=True,timeout=15)
    return p.stdout.strip() if p.returncode==0 else ''
versions={}
for root in ('/opt','/var/lib/pj3'):
    if not Path(root).exists():continue
    for directory,dirs,files in os.walk(root):
        depth=len(Path(directory).relative_to(root).parts)
        dirs[:]=[d for d in dirs if depth<5 and d not in ('.git','.cache','node_modules','venv','venvs','site-packages','out','logs','work','checkpoints')]
        for f in files:
            for family,pattern in [('spark',r'spark-core_[\d.]+-([\d.]+)\.jar'),('kafka',r'kafka_[\d.]+-([\d.]+)\.jar'),('nifi',r'nifi-runtime-([\d.]+)\.jar')]:
                m=re.fullmatch(pattern,f)
                if m:versions.setdefault(family,set()).add(m[1])
        if 'RELEASE' in files and 'spark' in directory.lower():
            line=Path(directory,'RELEASE').read_text(errors='replace').splitlines()[0]
            if re.match(r'Spark [\d.]+',line):versions.setdefault('spark',set()).add(line[:120])
for family,unit in [('garage','pj3-garage'),('prometheus','pj3-prometheus'),('grafana','pj3-grafana')]:
    pid=run(['systemctl','show',unit,'-p','MainPID','--value'])
    if pid.isdigit() and int(pid)>0:
        exe=Path('/proc',pid,'exe').resolve()
        if family in exe.name.lower():
            text=run([str(exe),'--version'])
            if text:versions.setdefault(family,set()).add(text.splitlines()[0][:160])
units=run(['systemctl','list-units','--type=service','--state=running','--no-legend','--plain'])
active=[line.split()[0] for line in units.splitlines() if re.match(r'(pj3-|paldo-|caddy|mysql|mongod)',line)]
print(json.dumps({'versions':{k:sorted(v) for k,v in versions.items()},'active_units':active}))

# 노트북 호스트에서 실행하는 VM 점검(C-05-01). VMware Workstation이 설치된 Windows 노트북용.
# 사용: powershell -ExecutionPolicy Bypass -File ops\inventory_host.ps1 [-VmRoot "<VM 폴더>"]
# 출력: VM마다 한 줄(논리 서버명, 기동, vCPU, RAM, 디스크 할당/호스트 점유, 스냅샷 수)과 호스트 디스크 여유, VM 디스크 합계.
# IP·계정은 출력하지 않는다. 결과는 ops/inventory_check.md 표에 옮긴다.
param([string]$VmRoot = "$env:USERPROFILE\Documents\Virtual Machines")

# 3차 VM 폴더 이름 → 4차 논리 서버명(기획서 6.3)
$Logical = @{
  'bastion' = 'bastion-01'; 'pj3-bastion' = 'bastion-01'; 'pj3-webapp' = 'app-01'; 'pj3-ad-server' = 'ad-01'; 'pj3-monitor' = 'metrics-01'
  'pj3-spark-master' = 'spark-master-01'; 'pj3-spark-worker01' = 'spark-worker-01'; 'pj3-spark-worker02' = 'spark-worker-02'
  'pj3-spark-worker03' = 'spark-worker-03'; 'pj3-nifi' = 'nifi-01'; 'pj3-kafka01' = 'kafka-01'; 'pj3-kafka02' = 'kafka-02'
  'pj3-kafka03' = 'kafka-03'; 'pj3-mysql01' = 'mysql-01'; 'pj3-mysql02' = 'mysql-02'; 'pj3-mysql03' = 'mysql-03'
  'pj3-mongo01' = 'mongo-01'; 'pj3-mongo02' = 'mongo-02'; 'pj3-mongo03' = 'mongo-03'; 'pj3-object01' = 'media-storage-01'
  'pj3-object02' = 'lake-storage-01'
}

$vmrun = "C:\Program Files\VMware\VMware Workstation\vmrun.exe"
if (-not (Test-Path $vmrun)) { $vmrun = "C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe" }
$running = $null
if (Test-Path $vmrun) { $out = & $vmrun list 2>$null; if ($LASTEXITCODE -eq 0) { $running = $out } }
if ($null -eq $running) { "경고: vmrun 실행 목록을 얻지 못했다. 기동 칸은 '알 수 없음'이다(VMware 설치 위치 확인)." }

function Get-AllocatedGB([string]$dir) {
  # 디스크 설명 파일(작은 .vmdk)의 RW 섹터 수 합. 스냅샷 델타(-000001.vmdk)는 제외.
  $sec = [int64]0
  Get-ChildItem $dir -Filter *.vmdk | Where-Object { $_.Length -lt 100KB -and $_.Name -notmatch '-\d{6}\.vmdk$' } | ForEach-Object {
    Get-Content $_.FullName -ErrorAction SilentlyContinue | Select-String '^RW (\d+)' | ForEach-Object { $sec += [int64]$_.Matches.Groups[1].Value }
  }
  [math]::Round($sec * 512 / 1GB, 0)
}

$sumAlloc = 0; $sumUsed = 0
"논리 서버명 | 기동 | vCPU | RAM(GB) | 디스크 할당(GB) | 디스크 호스트 점유(GB) | 스냅샷 수 | VM 폴더"
Get-ChildItem $VmRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  $vmx = Get-ChildItem $_.FullName -Filter *.vmx | Select-Object -First 1
  if (-not $vmx) { return }
  $c = Get-Content $vmx.FullName
  $cpu = ($c | Select-String '^numvcpus\s*=\s*"(\d+)"').Matches.Groups | Select-Object -Last 1 -ExpandProperty Value
  $mem = ($c | Select-String '^memsize\s*=\s*"(\d+)"').Matches.Groups | Select-Object -Last 1 -ExpandProperty Value
  if (-not $cpu) { $cpu = 1 }
  $used = [math]::Round(((Get-ChildItem $_.FullName -Recurse -Filter *.vmdk | Measure-Object Length -Sum).Sum / 1GB), 1)
  $alloc = Get-AllocatedGB $_.FullName
  $vmsd = Get-ChildItem $_.FullName -Filter *.vmsd | Select-Object -First 1
  $snap = 0; if ($vmsd) { $snap = (Get-Content $vmsd.FullName | Select-String '^snapshot\d+\.uid').Count }
  $on = if ($null -eq $running) { '알 수 없음' } elseif ($running -match [regex]::Escape($vmx.FullName)) { '켜짐' } else { '꺼짐' }
  $name = if ($Logical.ContainsKey($_.Name)) { $Logical[$_.Name] } else { "(프로젝트 밖) $($_.Name)" }
  if ($Logical.ContainsKey($_.Name)) { $sumAlloc += $alloc; $sumUsed += $used }
  "{0} | {1} | {2} | {3} | {4} | {5} | {6} | {7}" -f $name, $on, $cpu, [math]::Round([int]$mem / 1024, 1), $alloc, $used, $snap, $_.Name
}
$drv = (Resolve-Path $VmRoot -ErrorAction SilentlyContinue).Drive
if (-not $drv) { $drv = Get-PSDrive C }
"호스트 {0}: 드라이브(VM 폴더 위치) 여유 {1}GB / 전체 {2}GB" -f $drv.Name, [math]::Round($drv.Free / 1GB, 1), [math]::Round(($drv.Used + $drv.Free) / 1GB, 1)
"프로젝트 VM 디스크 합계: 할당 {0}GB, 호스트 점유 {1}GB" -f $sumAlloc, [math]::Round($sumUsed, 1)
$os = Get-CimInstance Win32_OperatingSystem
"호스트 RAM: 전체 {0}GB, 여유 {1}GB" -f [math]::Round($os.TotalVisibleMemorySize / 1MB, 1), [math]::Round($os.FreePhysicalMemory / 1MB, 1)

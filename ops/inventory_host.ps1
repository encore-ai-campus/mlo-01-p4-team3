# 노트북 호스트에서 실행하는 VM 점검(C-05-01). VMware Workstation이 설치된 Windows 노트북용.
# 사용: powershell -ExecutionPolicy Bypass -File ops\inventory_host.ps1 [-VmRoot "<VM 폴더>"]
# 출력: VM마다 한 줄(논리 서버명, 기동, vCPU, RAM, 디스크 할당/호스트 점유, 스냅샷 수)과 디스크별(OS·데이터) 한 줄씩, 호스트 디스크 여유, VM 디스크 합계.
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

function Read-VmdkText([string]$path) {
  # 작은 설명 파일은 그대로, 단일 파일(monolithic) 디스크는 앞 64KB에 설명이 들어 있다.
  if ((Get-Item $path).Length -lt 100KB) { return (Get-Content $path -Raw -ErrorAction SilentlyContinue) }
  $fs = [System.IO.File]::OpenRead($path)
  try { $buf = New-Object byte[] 65536; $n = $fs.Read($buf, 0, $buf.Length); return [System.Text.Encoding]::ASCII.GetString($buf, 0, $n) } finally { $fs.Close() }
}

function Get-VmDisks([string]$dir, [string[]]$vmxLines) {
  # .vmx의 디스크 항목(scsi0:0, scsi0:1 …)마다 할당 크기와 호스트 점유를 구한다.
  # 스냅샷이 있으면 .vmx는 델타(-000001.vmdk)를 가리키므로 parentFileNameHint를 따라가 기준 디스크의 RW 크기를 읽는다.
  $out = @()
  $vmxLines | Select-String '^((scsi|nvme|sata|ide)\d+:\d+)\.fileName\s*=\s*"([^"]+\.vmdk)"' | ForEach-Object {
    $slot = $_.Matches.Groups[1].Value; $leaf = Split-Path $_.Matches.Groups[3].Value -Leaf
    $f = Join-Path $dir $leaf; $sec = [int64]0; $guard = 0
    while ($f -and (Test-Path $f) -and $guard -lt 10) {
      $t = Read-VmdkText $f
      $s = [int64]0; [regex]::Matches($t, '(?m)^RW (\d+)') | ForEach-Object { $s += [int64]$_.Groups[1].Value }
      if ($s -gt 0) { $sec = $s }
      $p = [regex]::Match($t, '(?m)^parentFileNameHint="([^"]+)"')
      if ($p.Success) { $f = Join-Path $dir (Split-Path $p.Groups[1].Value -Leaf) } else { $f = $null }
      $guard++
    }
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($leaf) -replace '-\d{6}$', ''
    $pat = '^' + [regex]::Escape($stem) + '(-\d{6})?(-[sf]\d{3})?\.vmdk$'
    $used = (Get-ChildItem $dir -Filter *.vmdk | Where-Object { $_.Name -match $pat } | Measure-Object Length -Sum).Sum
    $out += [pscustomobject]@{ Slot = $slot; AllocGB = [math]::Round($sec * 512 / 1GB, 0); UsedGB = [math]::Round($used / 1GB, 1) }
  }
  $out
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
  $disks = @(Get-VmDisks $_.FullName $c)
  $alloc = ($disks | Measure-Object AllocGB -Sum).Sum; if (-not $alloc) { $alloc = 0 }
  $allocText = if ($disks.Count -gt 1) { "$alloc (" + (($disks | ForEach-Object { $_.AllocGB }) -join ' + ') + ")" } else { "$alloc" }
  $vmsd = Get-ChildItem $_.FullName -Filter *.vmsd | Select-Object -First 1
  $snap = 0; if ($vmsd) { $snap = (Get-Content $vmsd.FullName | Select-String '^snapshot\d+\.uid').Count }
  $on = if ($null -eq $running) { '알 수 없음' } elseif ($running -match [regex]::Escape($vmx.FullName)) { '켜짐' } else { '꺼짐' }
  $name = if ($Logical.ContainsKey($_.Name)) { $Logical[$_.Name] } else { "(프로젝트 밖) $($_.Name)" }
  if ($Logical.ContainsKey($_.Name)) { $sumAlloc += $alloc; $sumUsed += $used }
  "{0} | {1} | {2} | {3} | {4} | {5} | {6} | {7}" -f $name, $on, $cpu, [math]::Round([int]$mem / 1024, 1), $allocText, $used, $snap, $_.Name
  foreach ($d in $disks) { "    디스크 {0}: 할당 {1}GB, 호스트 점유 {2}GB" -f $d.Slot, $d.AllocGB, $d.UsedGB }
}
$drv = (Resolve-Path $VmRoot -ErrorAction SilentlyContinue).Drive
if (-not $drv) { $drv = Get-PSDrive C }
"호스트 {0}: 드라이브(VM 폴더 위치) 여유 {1}GB / 전체 {2}GB" -f $drv.Name, [math]::Round($drv.Free / 1GB, 1), [math]::Round(($drv.Used + $drv.Free) / 1GB, 1)
"프로젝트 VM 디스크 합계: 할당 {0}GB, 호스트 점유 {1}GB" -f $sumAlloc, [math]::Round($sumUsed, 1)
$os = Get-CimInstance Win32_OperatingSystem
"호스트 RAM: 전체 {0}GB, 여유 {1}GB" -f [math]::Round($os.TotalVisibleMemorySize / 1MB, 1), [math]::Round($os.FreePhysicalMemory / 1MB, 1)

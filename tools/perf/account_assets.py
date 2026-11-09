"""B-10-08: 로컬 WOFF2 용량과 명시적 미리보기의 브라우저 Timing 표본을 기록한다."""
import argparse
import json
from pathlib import Path
from tools.assets.sources import JsonLogger
from tools.evidence_result import write_result


def measure(root, sample):
    """디스크의 전체 글꼴과 실제 첫 화면에서 받은 글꼴·로드 시점을 따로 계산한다."""
    if sample['path'] != '/accounts/login' or sample['width'] not in (360, 1280):
        raise ValueError('1280 또는 360px 로그인 첫 화면 표본이 필요함')
    font_files = list((root / 'web/static/fonts').rglob('*.woff2'))
    if not font_files or not sample['fonts'] or sample['font_ready_ms'] <= 0:
        raise ValueError('글꼴 또는 브라우저 Timing 표본 없음')
    return {
        'font_total_bytes': {'value': sum(p.stat().st_size for p in font_files), 'unit': 'bytes'},
        'font_file_count': {'value': len(font_files), 'unit': '개'},
        'initial_font_bytes': {'value': sum(r['bytes'] for r in sample['fonts']), 'unit': 'bytes'},
        'initial_font_requests': {'value': len(sample['fonts']), 'unit': '개'},
        'initial_load_ms': {'value': round(sample['load_ms'], 3), 'unit': 'ms'},
        'font_ready_ms': {'value': round(sample['font_ready_ms'], 3), 'unit': 'ms'},
        'viewport_width': {'value': sample['width'], 'unit': 'px'},
        'horizontal_overflow': {'value': int(sample['overflow']), 'unit': '건'},
        'broken_images': {'value': sample['broken_images'], 'unit': '건'},
    }


def main():
    """ui_theme_preview --font-probe의 DOM 표본을 검증해 공통 결과 형식으로 쓴다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-sample', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    args = parser.parse_args()
    logger = JsonLogger(args.repo, service='perf', component='account_assets')
    logger.log('info', 'account_assets_start')
    sample = json.loads(args.browser_sample.read_text(encoding='utf-8'))
    metrics = measure(args.repo, sample)
    logger.log('info', 'font_bytes_measured', total=metrics['font_total_bytes']['value'],
               initial=metrics['initial_font_bytes']['value'])
    sample_name = '<browser-sample>.json' if args.browser_sample.is_absolute() else args.browser_sample.as_posix()
    path = write_result('B-10-08', 'login-' + str(sample['width']), metrics,
                        'python -B -m tools.perf.account_assets --browser-sample ' + sample_name, root=args.repo)
    logger.log('info', 'account_assets_complete', path=str(path.relative_to(args.repo)),
               load_ms=metrics['initial_load_ms']['value'])


if __name__ == '__main__':
    main()

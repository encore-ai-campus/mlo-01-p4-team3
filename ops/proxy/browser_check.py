"""Linux 격리 Chrome으로 실제 가입·로그인·HTTPS·wss를 확인한다."""
import argparse
import json
import os
from pathlib import Path
import secrets
import uuid
from urllib.parse import urlsplit


def write_private(path,data):
    """브라우저가 직접 얻은 테스트 상태는 같은 호스트의 보호 파일에만 둔다."""
    path.write_text(json.dumps(data)+'\n');path.chmod(0o600)


def check(origin,directory,phase,certificate_interstitial=False):
    """새 합성 계정으로 검증한다. 기존 서버 세션이나 계정 자격정보를 가져오지 않는다."""
    from playwright.sync_api import sync_playwright
    directory=Path(directory);directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    actor_path=directory/'actor-private.json';state_path=directory/'state-private.json'
    if phase=='register':
        if actor_path.exists():raise ValueError('actor_already_exists')
        actor={'username':'c07_ui_'+uuid.uuid4().hex[:10],'password':secrets.token_urlsafe(24)+'9a!'}
        write_private(actor_path,actor)
    else:actor=json.loads(actor_path.read_text())
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        # 내부 CA 예외는 이 새 자동 검증 컨텍스트에만 적용한다. OS 신뢰 저장소는 바꾸지 않는다.
        context=browser.new_context(ignore_https_errors=not certificate_interstitial,viewport={'width':1280,'height':900},
            **({'storage_state':str(state_path)} if phase!='register' else {}))
        page=context.new_page();ws=[];mixed=[];external_ad=[];errors=[]
        cdp=context.new_cdp_session(page);cdp.send('Network.enable')
        cdp.on('Network.webSocketHandshakeResponseReceived',lambda event:ws.append(event['response']['status']))
        page.on('pageerror',lambda error:errors.append(type(error).__name__))
        page.on('console',lambda message:mixed.append(message.type) if 'mixed content' in message.text.lower() else None)
        page.on('request',lambda request:external_ad.append(True) if urlsplit(request.url).hostname!=urlsplit(origin).hostname and '/ads/' in request.url else None)
        warning_bypassed=False
        try:page.goto(origin+('/accounts/signup' if phase=='register' else '/'))
        except Exception:
            if not certificate_interstitial:raise
            page.get_by_role('button',name='Advanced',exact=True).click()
            page.get_by_role('link',name='Proceed to '+urlsplit(origin).hostname+' (unsafe)',exact=True).click()
            page.wait_for_load_state('domcontentloaded');warning_bypassed=True
        if phase=='register':
            page.locator('input[name=username]').fill(actor['username']);page.locator('input[name=password]').fill(actor['password'])
            page.locator('input[name=password_confirm]').fill(actor['password']);page.locator('input[name=consent]').check()
            page.get_by_role('button',name='가입',exact=True).click();page.wait_for_url(origin+'/')
            page.get_by_role('button',name='로그아웃',exact=True).click();page.wait_for_url('**/accounts/login')
            page.locator('input[name=username]').fill(actor['username']);page.locator('input[name=password]').fill(actor['password'])
            page.get_by_role('button',name='로그인',exact=True).click();page.wait_for_url(origin+'/')
            write_private(state_path,context.storage_state())
            # 공개된 합성 식별자만 운영자의 회차 배정에 사용한다. 암호·쿠키는 출력하지 않는다.
            print(json.dumps({'registration_username':actor['username'],'signup_and_login':True}))
        else:
            resume=page.get_by_role('link',name='1번째 판 이어 하기',exact=True)
            if resume.count():resume.click()
            else:page.get_by_role('button',name='1번째 판 시작',exact=True).click()
            page.wait_for_url('**/games/**');page.wait_for_timeout(1500)
            cookies={c['name']:c for c in context.cookies()}
            result={'game_started':True,'wss_101':101 in ws,'websocket_statuses':ws,
                'session_secure':cookies.get('sessionid',{}).get('secure',False),
                'csrf_secure':cookies.get('csrftoken',{}).get('secure',False),
                'mixed_content':len(mixed),'external_ad_requests':len(external_ad),'page_errors':len(errors),
                'browser':browser.version,'certificate_exception':'browser_warning_ui' if certificate_interstitial else 'isolated_test_context_only',
                'warning_bypassed':warning_bypassed,'ignore_https_errors':not certificate_interstitial}
            page.screenshot(path=str(directory/'game.png'),full_page=True)
            (directory/'result.json').write_text(json.dumps(result)+'\n');print(json.dumps(result))
            if not result['wss_101'] or not result['session_secure'] or not result['csrf_secure'] or mixed or external_ad or errors:
                raise RuntimeError('browser_acceptance_failed')
        context.close();browser.close()


def main(argv=None):
    """접속 주소는 비공개 인자로 받고 신규 테스트 브라우저의 상태를 호스트 밖으로 내보내지 않는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--origin',required=True);parser.add_argument('--directory',required=True)
    parser.add_argument('--phase',choices=['register','game'],required=True)
    parser.add_argument('--certificate-interstitial',action='store_true');args=parser.parse_args(argv)
    check(args.origin.rstrip('/'),args.directory,args.phase,args.certificate_interstitial)


if __name__=='__main__':main()

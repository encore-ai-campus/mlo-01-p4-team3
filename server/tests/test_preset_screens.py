"""공개 프리셋·초상·8인 완주·거절 로그·심문 입력의 B-08-02 계약을 검사한다."""
from dataclasses import replace
from html.parser import HTMLParser
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth.models import User
from django.test import TestCase
from server.app.open_start import open_preset_ids
from server.app.usecases import StartUnavailable
from server.paldo_web.composition import configure_game_service
from .preset_fixture import make_preset_service
from .support import install_test_profiles


class Inputs(HTMLParser):
    """렌더된 밤 선택지와 질문의 비활성 상태를 읽는다."""
    def __init__(self, html):
        super().__init__()
        self.select = None
        self.options = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        """select 이름과 option 속성을 기록한다."""
        attrs = dict(attrs)
        if tag == 'select': self.select = attrs.get('name')
        if tag == 'option': self.options.append((self.select, attrs))

    def handle_endtag(self, tag):
        """select 범위를 닫는다."""
        if tag == 'select': self.select = None


class PresetScreens(TestCase):
    """실제 Django 경로에서 게임 규칙과 공개 정보 보존을 확인한다."""
    def setUp(self):
        """동의한 합성 계정과 격리된 시험 서비스를 준비한다."""
        self.user = User.objects.create_user(username='preset-player')
        self.player = str(install_test_profiles(self)(self.user).player_pseudonym)
        self.client.force_login(self.user)
        self.service = make_preset_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)

    def start(self, **values):
        """새 시작 UUID로 공개 일반 판을 시작한다."""
        return self.client.post('/games', {'preset_id':'base6', 'mudang_variant':'f',
            'start_token':str(uuid4()), **values}, content_type='application/json', HTTP_ACCEPT='application/json')

    def test_config_file_changes_home_and_start_without_code_change(self):
        """설정 파일만 바꾸면 홈 선택지와 시작 허용이 함께 바뀐다."""
        self.assertEqual(open_preset_ids(), ['base6'])
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'enabled.json'
            with patch('server.app.open_start.PRESETS_CONFIG', path):
                path.write_text('["base6"]', encoding='utf-8')
                self.assertNotContains(self.client.get('/'), 'value="large8"')
                self.assertEqual(self.start(preset_id='large8').status_code, 400)
                path.write_text('["base6","large8"]', encoding='utf-8')
                self.assertContains(self.client.get('/'), 'value="large8"')
                self.assertEqual(self.start(preset_id='large8').status_code, 200)
                path.write_text('["large8"]', encoding='utf-8')
                self.assertEqual(self.start().status_code, 400)
                path.write_text('["unknown"]', encoding='utf-8')
                with self.assertRaises(StartUnavailable): open_preset_ids()
                self.assertContains(self.client.get('/'), '지금은 게임을 시작할 수 없습니다.')

    def test_portraits_metadata_and_fallback_do_not_change_rules(self):
        """초상 두 값은 메타와 HTML에 반영되고 상태·이벤트 판정은 바꾸지 않는다."""
        views=[]
        for variant in ('f','m'):
            response=self.start(mudang_variant=variant)
            self.assertEqual(response.status_code,200)
            gid=response.json()['view']['game_id']
            state,_=self.service.repository.load(gid)
            self.assertEqual(state.meta.mudang_variant,variant)
            page=self.client.get('/games/'+gid,HTTP_ACCEPT='text/html')
            self.assertContains(page,'/static/assets/portrait/mudang_'+variant+'.png')
            views.append(dict(response.json()['view'],game_id='same'))
        self.assertEqual(views[0],views[1])
        self.assertEqual(self.start(mudang_variant='other').status_code,400)
        gid=self.start().json()['view']['game_id']
        state,truth=self.service.repository.load(gid)
        self.service.repository.docs[gid]['state']=replace(state,meta=replace(state.meta,mudang_variant=None))
        self.assertContains(self.client.get('/games/'+gid,HTTP_ACCEPT='text/html'),'/static/assets/portrait/mudang_f.png')
        home=self.client.get('/')
        self.assertContains(home,'type="radio" name="mudang_variant" value="f"')
        self.assertContains(home,'type="radio" name="mudang_variant" value="m"')
        from engine.domain.engine import PaldoEngine
        engine=PaldoEngine()
        meta=replace(state.meta,seed=7)
        deps=self.service.dependencies(meta)
        female=engine.new_game(replace(meta,mudang_variant='f'),deps)
        male=engine.new_game(replace(meta,mudang_variant='m'),deps)
        self.assertEqual(replace(female.state,meta=male.state.meta),male.state)
        self.assertEqual(female.truth,male.truth)
        self.assertEqual([(e.event_type,e.payload) for e in female.events],[(e.event_type,e.payload) for e in male.events])

    def test_large8_recording_renders_all_frames_without_private_state(self):
        """공용 엔진으로 생성한 8인 녹화 판 15단계를 HTTP·전체/조각 HTML로 완주한다."""
        with patch('server.app.open_start.open_preset_ids',return_value=['base6','large8']):
            response=self.start(preset_id='large8')
        self.assertEqual(response.status_code,200)
        gid=response.json()['view']['game_id']
        recording=self.service.engine.recordings['large8'].steps
        for index,frame in enumerate(recording):
            if index:
                body=dict(frame['request'],game_id=gid,command_id=str(uuid4()))
                response=self.client.post('/games/'+gid+'/commands',body,content_type='application/json',HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['view'],dict(frame['view'],game_id=gid))
            for headers in ({'HTTP_ACCEPT':'text/html'},{'HTTP_HX_REQUEST':'true'}):
                page=self.client.get('/games/'+gid,**headers)
                self.assertEqual(page.status_code,200)
                html=page.content.decode()
                for forbidden in ('truth','pending_votes','npc_beliefs','npc_private'):
                    self.assertNotIn(forbidden,html)
                self.assertEqual(len(page.context['vm'].people),len(frame['view']['names']))
        self.assertEqual(response.json()['view']['phase'],'ended')

    def test_rejection_log_contains_enum_only_for_rejected_command(self):
        """JSON·htmx 거절 로그에 코드만 추가하며 수락/재생과 요청 원문은 기록하지 않는다."""
        for headers in ({'HTTP_ACCEPT':'application/json'},{'HTTP_HX_REQUEST':'true'}):
            gid=self.start().json()['view']['game_id']
            body=dict(game_id=gid,command_id=str(uuid4()),expected_state_version=0,
                      command=dict(kind='explain',actor='mudang',target='private-marker'))
            with self.assertLogs('paldo.web',level='INFO') as logs:
                rejected=self.client.post('/games/'+gid+'/commands',body,content_type='application/json',**headers)
            self.assertEqual(rejected.status_code,422)
            self.assertEqual(logs.records[-1].fields['reject'],'illegal_phase')
            self.assertNotIn('private-marker',json.dumps(logs.records[-1].fields))
            body['command']['kind']='advance'
            with self.assertLogs('paldo.web',level='INFO') as logs:
                accepted=self.client.post('/games/'+gid+'/commands',body,content_type='application/json',**headers)
                replay=self.client.post('/games/'+gid+'/commands',body,content_type='application/json',**headers)
            self.assertEqual((accepted.status_code,replay.status_code),(200,200))
            self.assertTrue(all('reject' not in record.fields for record in logs.records if record.msg=='http_request'))

    def test_interrogation_form_exposes_only_past_nights_and_guides_evidence(self):
        """지난 밤만 선택할 수 있고 근거 부족 시 모순 질문을 안내·제한한다."""
        gid=self.start().json()['view']['game_id']
        for step in self.service.engine.recordings['base6'].steps[1:3]:
            advanced=self.client.post('/games/'+gid+'/commands',dict(step['request'],game_id=gid,command_id=str(uuid4())),content_type='application/json',HTTP_ACCEPT='application/json')
            self.assertEqual(advanced.status_code,200)
        page=self.client.get('/games/'+gid,HTTP_ACCEPT='text/html')
        form=Inputs(page.content.decode())
        self.assertEqual([a['value'] for name,a in form.options if name=='night'],[str(n) for n in range(page.context['vm'].day)])
        self.assertContains(page,'관련 진술 두 개')
        view=self.service.engine.recordings['base6']._view(gid,2)
        from server.api.meeting_view import meeting_context
        small=replace(view,public=replace(view.public,claims=[]))
        context=meeting_context(small)
        self.assertFalse(context['contradiction_ready'])
        self.assertEqual(context['past_nights'],list(range(view.day)))
        with patch.object(self.service.engine.recordings['base6'],'_view',return_value=small):
            self.assertContains(self.client.get('/games/'+gid,HTTP_ACCEPT='text/html'),'value="contradiction" disabled')

    def test_real_engine_accepts_valid_interrogation_and_rejects_tampered_inputs(self):
        """UI 제한과 별개로 실제 엔진도 잘못된 밤·근거를 거부하고 정상 질문은 수락한다."""
        from engine.domain.engine import PaldoEngine
        from engine.domain.model import Phase
        from tests.engine.test_scenarios_engine import game_base6, drive_to
        engine=PaldoEngine()
        state,truth=game_base6(engine)
        state,truth=drive_to(self,engine,state,truth,Phase.INTERROGATION)
        state=replace(state,meta=replace(state.meta,player_pseudonym=self.player))
        self.service.engine=engine
        self.service.repository.create(state,truth,[])
        target=next(n for n in state.active if n!='mudang')
        command=dict(kind='interrogate',actor='mudang',addressee=target,
                     interrogation_kind='whereabouts',night=state.day,slot='evening')
        def submit(value):
            return self.client.post('/games/'+state.meta.game_id+'/commands',dict(game_id=state.meta.game_id,
                command_id=str(uuid4()),expected_state_version=state.state_version,command=value),
                content_type='application/json',HTTP_ACCEPT='application/json')
        self.assertEqual(submit(command).status_code,422)
        self.assertEqual(submit(dict(command,interrogation_kind='contradiction',evidence=[])).status_code,422)
        pair=[{'type':'claim','record_id':claim.claim_id} for claim in state.public.claims[:2]]
        accepted=submit(dict(command,interrogation_kind='contradiction',evidence=pair))
        self.assertEqual(accepted.status_code,200)
        state=replace(state,state_version=accepted.json()['view']['state_version'])
        self.assertEqual(submit(dict(command,night=0)).status_code,200)

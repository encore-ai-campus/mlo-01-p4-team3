"""명시적으로 켠 로컬 미리보기에서만 Navigation/Resource Timing을 DOM에 남긴다."""
SCRIPT = r'''<script>
window.addEventListener('load', () => {
  document.fonts.ready.then(() => requestAnimationFrame(() => {
    const nav = performance.getEntriesByType('navigation')[0];
    const fonts = performance.getEntriesByType('resource').filter(r => new URL(r.name).pathname.endsWith('.woff2'));
    const sample = {
      width: innerWidth, height: innerHeight, path: location.pathname,
      load_ms: nav.loadEventEnd,
      font_ready_ms: performance.now(),
      fonts: fonts.map(r => ({path: new URL(r.name).pathname, bytes: r.encodedBodySize, end_ms: r.responseEnd})),
      overflow: document.documentElement.scrollWidth > innerWidth,
      broken_images: [...document.images].filter(i => !i.complete || !i.naturalWidth).length
    };
    const out = document.createElement('output');
    out.id = 'font-probe'; out.hidden = true;
    out.dataset.sample = JSON.stringify(sample); document.body.append(out);
  }));
}, {once:true});
</script>'''


class FontProbe:
    """ui_theme_preview --font-probe가 주입한 때만 로그인 HTML에 계측을 붙인다."""

    def __init__(self, get_response):
        """기존 응답 처리 함수를 보관한다."""
        self.get_response = get_response

    def __call__(self, request):
        """익명 로그인 GET의 브라우저 계측만 추가하고 운영 요청·API는 바꾸지 않는다."""
        response = self.get_response(request)
        if request.path == '/accounts/login' and request.method == 'GET' and response.status_code == 200:
            if response.get('Content-Type', '').startswith('text/html'):
                response.content = response.content.replace(b'</body>', SCRIPT.encode('utf-8') + b'</body>')
                response.headers.pop('Content-Length', None)
        return response

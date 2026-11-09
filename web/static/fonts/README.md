# 화면 글꼴 출처

B-10-08의 확정 시안 글꼴을 서버의 정적 파일로 제공한다. 외부 CDN 요청은 없다.

| 글꼴 | 제공 파일 | 출처·라이선스 |
|---|---|---|
| Gowun Batang 400·700 | `GowunBatang-Regular.woff2`, `GowunBatang-Bold.woff2` | 승인 시안에 포함된 WOFF2 그대로. [제작자 저장소](https://github.com/yangheeryu/Gowun-Batang), SIL OFL 1.1 전문 `GowunBatang-OFL.txt` |
| Pretendard Variable v1.3.9 | `woff2-dynamic-subset/` 92개 | [공식 배포본](https://github.com/orioncactus/pretendard/tree/v1.3.9/packages/pretendard/dist/web/variable/woff2-dynamic-subset), SIL OFL 1.1 전문 `Pretendard-OFL.txt` |

`pretendard-subset.css`는 공식 `unicode-range` 선언과 상대 파일 경로를 유지한다. 화면에 필요한 범위만 내려받으며 전체 `PretendardVariable.woff2`는 포함하지 않는다. `fonts.css`는 두 글꼴의 선언·공통 토큰을 제공하고 `css/theme.css`가 가져온다. 모든 WOFF2의 디스크 합계와 실제 첫 화면에서 받은 파일 용량은 다르다. 측정은 `tools/perf/account_assets.py`와 B-10-08 증빙을 따른다.

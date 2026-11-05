"""E-05-05 임시 그림 생성기(코드로 그린 실루엣·배치 확인용, 최종 화풍 아님): 기준 색·생성기 블록·결정성·주인공/주민 판정표 규칙."""
from __future__ import annotations

import re
import unittest

from tools.assets import art, png, portrait_art as pa, sources

ROOT = sources.ROOT
GUIDE = ROOT / "docs" / "assets" / "style_guide.md"
NPC_IDS = sources.load_npc_ids()
ALL_IDS = ["mudang_f", "mudang_m", *NPC_IDS]
HEROES = {"mudang_f", "mudang_m"}


def _guide_palette() -> list[tuple[str, str]]:
    """스타일 가이드 3절 표의 (이름, 코드) 목록."""
    text = GUIDE.read_text(encoding="utf-8")
    sec = text.split("## 3.", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\| ([^|]+?) \| `(#[0-9A-Fa-f]{6})` \|", sec, re.M)


def _white_components(g: pa.Grid, box: tuple[int, int, int, int]) -> int:
    """눈 영역 안 하이라이트(백) 픽셀의 4연결 덩어리 수."""
    x0, y0, x1, y1 = box
    cells = {(x, y) for y in range(y0, y1) for x in range(x0, x1) if g.px[y][x] == pa.WHITE}
    count = 0
    while cells:
        stack = [cells.pop()]
        count += 1
        while stack:
            x, y = stack.pop()
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in cells:
                    cells.remove(n)
                    stack.append(n)
    return count


class PortraitArtTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = {cid: pa.render(cid, 512, 512) for cid in ALL_IDS}

    def test_palette_matches_style_guide_table(self):
        self.assertEqual(list(pa.PALETTE.items()), _guide_palette())
        self.assertEqual(len(pa.PALETTE), 17)

    def test_covers_all_portrait_ids(self):
        self.assertEqual(len(ALL_IDS), 13)
        self.assertEqual(set(pa.CHARACTERS), set(ALL_IDS))

    def test_only_palette_colors(self):
        allowed = {bytes(c) for c in pa.C.values()}
        for cid, rows in self.rows.items():
            seen = {bytes(r[i:i + 3]) for r in rows for i in range(0, len(r), 3)}
            self.assertLessEqual(seen, allowed, cid)

    def test_4x4_blocks_are_single_color(self):
        for cid, rows in self.rows.items():
            for gy in range(128):
                block_rows = rows[gy * 4:gy * 4 + 4]
                self.assertTrue(all(r == block_rows[0] for r in block_rows), (cid, gy))
                r = block_rows[0]
                for gx in range(128):
                    cell = r[gx * 12:gx * 12 + 12]
                    self.assertEqual(cell, cell[:3] * 4, (cid, gx, gy))

    def test_deterministic_and_distinct(self):
        for cid in ALL_IDS:
            self.assertEqual(pa.render(cid, 512, 512), self.rows[cid], cid)
        blobs = {b"".join(bytes(r) for r in rows) for rows in self.rows.values()}
        self.assertEqual(len(blobs), 13)

    def test_highlight_rule_hero_2_residents_1(self):
        """판정표: 주인공은 눈마다 하이라이트 2점, 주민은 전원 1점."""
        for cid in ALL_IDS:
            g = pa.draw(cid)
            want = 2 if cid in HEROES else 1
            for box in pa.EYE_BOXES:
                self.assertEqual(_white_components(g, box), want, (cid, box))

    def test_hero_eyes_symmetric(self):
        for cid in HEROES:
            g = pa.draw(cid)
            (lx0, y0, lx1, y1), _ = pa.EYE_BOXES
            skin = {pa.SKIN, pa.SKIN_S}       # 얼굴 오른쪽 그림자(빛 왼쪽 위)는 이목구비가 아니므로 뺀다
            for y in range(y0, y1):
                for x in range(lx0, lx1):
                    left, right = g.px[y][x], g.px[y][127 - x]
                    if left in skin and right in skin:
                        continue
                    self.assertEqual(left, right, (cid, x, y))

    def test_same_face_frame_for_everyone(self):
        """머리 크기·눈높이가 같다: 홍채(머리 하이라이트색)·하이라이트 자리가 모든 초상에서 같다."""
        def eyes(cid):
            g = pa.draw(cid)
            return {(x, y) for x0, y0, x1, y1 in pa.EYE_BOXES
                    for y in range(y0, y1) for x in range(x0, x1) if g.px[y][x] in (pa.HAIR_HI, pa.WHITE)}
        hero, resident = eyes("mudang_f"), eyes("hunjang")
        self.assertTrue(resident)
        for cid in ALL_IDS:
            self.assertEqual(eyes(cid), hero if cid in HEROES else resident, cid)
        self.assertLessEqual(len(hero ^ resident), 2 * len(pa.EYE_BOXES))   # 차이는 주인공 작은 하이라이트 점뿐

    def test_png_under_limit_and_art_wired(self):
        spec = sources.load_spec()
        for cid, rows in self.rows.items():
            data = png.encode_rgb(512, 512, rows)
            self.assertLessEqual(len(data), spec.max_bytes, cid)
            self.assertEqual(art.render("portrait", cid, 512, 512).rows, rows)

    def test_unknown_id_and_bad_size(self):
        self.assertEqual(pa.render("new_villager", 128, 128), pa.render("other_villager", 128, 128))
        for w, h in ((500, 500), (512, 256), (0, 0)):
            with self.assertRaises(ValueError):
                pa.render("jumo", w, h)

    def test_committed_portraits_match_generator(self):
        """커밋된 임시 초상이 지금 생성기와 같은 바이트다(표시가 true인 것만)."""
        spec = sources.load_spec()
        for e in sources.load_manifest(ROOT, spec):
            if e.kind != "portrait" or not e.placeholder:
                continue
            data = png.encode_rgb(e.width, e.height, pa.render(e.id, e.width, e.height))
            self.assertEqual((ROOT / e.path).read_bytes(), data, e.path)

    def test_committed_sprites_match_generator(self):
        """커밋된 임시 sprite가 지금 생성기(RGBA, 바탕 투명)와 같은 바이트다(표시가 true인 것만)."""
        spec = sources.load_spec()
        seen = 0
        for e in sources.load_manifest(ROOT, spec):
            if e.kind != "sprite" or not e.placeholder:
                continue
            data = png.encode_rgba(e.width, e.height, pa.render_sprite(e.id, e.width, e.height))
            self.assertEqual((ROOT / e.path).read_bytes(), data, e.path)
            seen += 1
        self.assertEqual(seen, 13)

    def test_sprite_bad_size(self):
        for w, h in ((512, 512), (500, 625), (0, 0)):
            with self.assertRaises(ValueError):
                pa.render_sprite("jumo", w, h)


if __name__ == "__main__":
    unittest.main()

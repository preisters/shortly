"""채널별 기초 템플릿(1080x1920 PNG) 생성기.

- 텍스트/아이콘/채널명은 넣지 않는다(캡컷에서 별도 삽입).
- 라인, 구역 구분 배경, 포인트 도형 등 '골격 요소'만 배치한다.
- 원본 레이아웃(모바일 기사형) 골격에 충실하게 맞추고, 채널별 차이는 색으로만 준다.
  원본 골격: [테두리 헤더 박스] + [가로 구분 띠] + [좌측 아이콘 사각형] + [이미지 박스]
- 2배 슈퍼샘플링 후 축소로 안티에일리어싱 처리.
"""

from pathlib import Path

from PIL import Image, ImageDraw

W, H = 1080, 1920
S = 2  # supersampling

# 공통 컬러
BG = "#FAF8F2"        # 본문 배경
DIVIDER = "#D9D4CC"   # 얇은 구분선(중립)
CARD_BORDER = "#D9D4CC"  # 이미지 슬롯 테두리(중립)
SLOT_FILL = "#F1EDE6"    # 이미지 슬롯 바탕(옅은 중립)

# 채널별 시그니처 컬러: (main, sub, tint)
CHANNELS = {
    "챙담": ("#F2C14E", "#74C7B8", "#FFF6DA"),
    "챙맛": ("#E85B3B", "#FFD166", "#FFF0E8"),
    "챙잇": ("#4C7DFF", "#63D2FF", "#EEF4FF"),
    "챙겜": ("#7C4DFF", "#55E6A5", "#F1ECFF"),
}


def s(v):
    return int(round(v * S))


def rr(draw, box, radius, **kw):
    draw.rounded_rectangle([s(box[0]), s(box[1]), s(box[2]), s(box[3])],
                           radius=s(radius), **kw)


def rect(draw, box, **kw):
    draw.rectangle([s(box[0]), s(box[1]), s(box[2]), s(box[3])], **kw)


def build(main, sub, tint):
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)

    # ※ 헤더는 원본에 테두리 박스가 없음(흰 바탕 위 텍스트뿐) → 아무 것도 그리지 않음.
    #    브랜드 문구/아이콘/채널명/제목/자막은 전부 캡컷에서 삽입.

    # ── 1. 가로 색 띠(채널 틴트) : 원본 초록 띠(세로 ≈9~13%) 자리 = 채널 헤더색 ──
    #    옅은 틴트라 상·하단 얇은 구분선으로 구역을 명확히(원본의 색 대비 대체).
    rect(d, (0, 177, W, 255), fill=tint)
    rect(d, (0, 177, W, 179), fill=DIVIDER)
    rect(d, (0, 253, W, 255), fill=DIVIDER)

    # ── 2. 좌측 배지 사각형(메인색) : 아이콘/글리프는 캡컷에서 ──
    #    원본 배지 위치(세로 ≈14~20%, 좌측)
    rr(d, (84, 264, 204, 384), 20, fill=main)

    # ── 3. 메인 이미지 슬롯 : 각진 모서리(원본 사진과 동일), 얇은 테두리 ──
    #    원본 사진 위치(세로 ≈39~85%, 폭 ≈74%, 중앙). 실제 이미지는 캡컷에서 위에 배치.
    rr(d, (138, 756, 942, 1638), 6, fill=SLOT_FILL,
       outline=CARD_BORDER, width=s(2))

    return img.resize((W, H), Image.LANCZOS)


def main_run():
    out_dir = Path(__file__).parent
    for name, (m, sub, tint) in CHANNELS.items():
        im = build(m, sub, tint)
        p = out_dir / f"template_{name}.png"
        im.save(p, "PNG")
        print("saved:", p)


if __name__ == "__main__":
    main_run()

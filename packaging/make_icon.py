"""用代码画应用图标（叠放的索引卡片），生成 PNG / ICNS / ICO，避免把二进制图标放进仓库。"""

from pathlib import Path

from PIL import Image, ImageDraw

PAPER = (255, 255, 255, 255)
INK = (46, 44, 41, 255)
FAINT = (169, 165, 157, 255)
ACCENT = (46, 107, 90, 255)
BG = (245, 244, 241, 255)


def draw(size: int = 1024) -> Image.Image:
    s = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([int(64 * s), int(64 * s), int(960 * s), int(960 * s)], radius=int(200 * s), fill=BG)
    back = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(back)
    bd.rounded_rectangle([int(300 * s), int(250 * s), int(820 * s), int(620 * s)], radius=int(56 * s),
                         fill=PAPER, outline=FAINT, width=max(2, int(18 * s)))
    img.alpha_composite(back.rotate(-7, center=(int(560 * s), int(435 * s)), resample=Image.BICUBIC))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([int(204 * s), int(392 * s), int(724 * s), int(762 * s)], radius=int(56 * s),
                        fill=PAPER, outline=INK, width=max(2, int(20 * s)))
    d.rounded_rectangle([int(278 * s), int(486 * s), int(650 * s), int(516 * s)], radius=int(15 * s), fill=ACCENT)
    return img


def build(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    img = draw()
    paths = {"png": out_dir / "icon.png", "icns": out_dir / "icon.icns", "ico": out_dir / "icon.ico"}
    img.save(paths["png"])
    img.save(paths["icns"])
    img.save(paths["ico"], sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return paths


if __name__ == "__main__":
    print(build(Path(__file__).parent / "build-icon"))

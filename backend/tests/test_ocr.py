"""图片和扫描版 PDF 的文字识别（没装识别组件时跳过）。"""

import io

import pytest
from fastapi.testclient import TestClient

from app.logic import ocr

pytestmark = pytest.mark.skipif(not ocr.available(), reason="没有安装文字识别组件")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    from app.main import app
    with TestClient(app) as c:
        yield c


def _image():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (900, 220), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 40)
        text = "临界区每次只允许一个进程进入"
    except OSError:
        font, text = ImageFont.load_default(size=40), "critical section mutex"
    d.text((30, 80), text, font=font, fill="black")
    return img, text


def test_image_and_scanned_pdf(client):
    img, text = _image()
    png, pdf = io.BytesIO(), io.BytesIO()
    img.save(png, "PNG")
    img.save(pdf, "PDF")
    cid = client.get("/api/courses").json()[0]["id"]
    files = [("files", ("board.png", png.getvalue(), "image/png")), ("files", ("scan.pdf", pdf.getvalue(), "application/pdf"))]
    res = client.post("/api/materials", data={"course_id": cid}, files=files).json()["results"]
    for r in res:
        m = client.get(f"/api/materials/{r['material_id']}").json()
        assert m["status"] == "ready", m
        unit = m["units"][0]
        assert text[:4] in unit["text"].replace(" ", "")
        assert any("识别" in w for w in unit["warning_text"])

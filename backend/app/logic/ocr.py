"""图片和扫描件文字识别（OCR）：在本机运行 RapidOCR（PaddleOCR 模型 + onnxruntime），不联网、不花钱。

识别组件是可选依赖（backend/requirements-ocr.txt）。没装时 available() 返回 False，资料照常上传，只是图片里的文字需要手动补充。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

_engine: Any = None
_lock = threading.Lock()
MAX_SIDE = 2400  # 太大的图先缩小，识别更快，效果基本不变
PDF_SCALE = 2.0  # 扫描版 PDF 按 144 DPI 渲染后识别
MAX_PDF_OCR_PAGES = 200


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        import rapidocr  # noqa: F401
    except Exception:
        return False
    return True


def _get_engine() -> Any:
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        _engine = RapidOCR()
    return _engine


def _to_lines(result: Any) -> str:
    """把识别出的文字框按行拼起来：同一行（纵向位置接近）的框用空格连接。"""
    txts = list(getattr(result, "txts", None) or [])
    boxes = getattr(result, "boxes", None)
    if not txts:
        return ""
    if boxes is None:
        return "\n".join(txts)
    items = []
    for text, box in zip(txts, boxes):
        pts = box.tolist() if hasattr(box, "tolist") else box
        ys = [p[1] for p in pts]
        xs = [p[0] for p in pts]
        items.append((min(ys), max(ys), min(xs), text))
    items.sort(key=lambda i: (i[0], i[2]))
    lines: list[list[tuple[float, float, float, str]]] = []
    for item in items:
        top, bottom = item[0], item[1]
        if lines:
            last = lines[-1]
            ltop = min(i[0] for i in last)
            lbottom = max(i[1] for i in last)
            overlap = min(bottom, lbottom) - max(top, ltop)
            if overlap > 0.5 * min(bottom - top, lbottom - ltop):
                last.append(item)
                continue
        lines.append([item])
    return "\n".join(" ".join(i[3] for i in sorted(line, key=lambda i: i[2])) for line in lines)


def _shrink(img: Any) -> Any:
    w, h = img.size
    if max(w, h) > MAX_SIDE:
        ratio = MAX_SIDE / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)))
    return img


def ocr_pil(img: Any) -> str:
    import numpy as np

    img = _shrink(img.convert("RGB"))
    with _lock:  # 模型实例不保证线程安全，一次识别一张
        result = _get_engine()(np.array(img)[:, :, ::-1])  # RGB → BGR
    return _to_lines(result)


def ocr_image_file(path: Path) -> str:
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)  # 手机拍的照片按拍摄方向摆正
        return ocr_pil(img)


def ocr_pdf_pages(path: Path, pages: list[int]) -> dict[int, str]:
    """识别 PDF 的指定页（从 1 开始），返回 {页码: 文字}。需要 pypdfium2 把页面渲染成图片。"""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return {}
    out: dict[int, str] = {}
    doc = pdfium.PdfDocument(str(path))
    try:
        for number in pages[:MAX_PDF_OCR_PAGES]:
            page = doc[number - 1]
            try:
                img = page.render(scale=PDF_SCALE).to_pil()
                out[number] = ocr_pil(img)
            finally:
                page.close()
    finally:
        doc.close()
    return out

from io import BytesIO

import qrcode
import zxingcpp
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


class InvalidQR(ValueError):
    pass


def make_qr(token: str) -> bytes:
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    code.add_data(token)
    code.make(fit=True)
    output = BytesIO()
    code.make_image(fill_color="black", back_color="white").save(output, format="PNG")
    return output.getvalue()


def decode_qr(data: bytes) -> str:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise InvalidQR("Пришлите фото размером до 8 МБ")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise InvalidQR("Изображение слишком большое: максимум 16 мегапикселей")
            image = ImageOps.exif_transpose(source).convert("L")
            # JPEG block artifacts can defeat LocalAverage on otherwise valid QR photos.
            codes = []
            for binarizer in (
                zxingcpp.Binarizer.LocalAverage,
                zxingcpp.Binarizer.GlobalHistogram,
            ):
                codes.extend(
                    zxingcpp.read_barcodes(
                        image, formats=zxingcpp.BarcodeFormat.QRCode, binarizer=binarizer
                    )
                )
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidQR("Не удалось открыть изображение") from exc
    tokens = {code.text.strip() for code in codes if code.valid and code.text.strip()}
    if not tokens:
        raise InvalidQR("QR не найден. Снимите ближе и без бликов или пришлите текст кода")
    if len(tokens) != 1:
        raise InvalidQR("На фото несколько QR. Пришлите один пропуск")
    return tokens.pop()

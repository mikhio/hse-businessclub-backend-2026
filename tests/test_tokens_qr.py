import uuid
from io import BytesIO

import pytest
from PIL import Image

from app.config import Settings
from app.qr import InvalidQR, decode_qr, make_qr
from app.tokens import InvalidPass, PassSigner


def test_pass_round_trip_has_no_personal_data(settings):
    signer = PassSigner(settings.pass_signing_key, settings.event_id)
    pass_id = uuid.uuid4()
    token = signer.issue(pass_id)
    assert signer.verify(token) == pass_id
    assert "9001" not in token


@pytest.mark.parametrize("bad", ["", "123", "hse1.not-base64.signature", "a.b.c.d", "x" * 301])
def test_invalid_token(settings, bad):
    with pytest.raises(InvalidPass):
        PassSigner(settings.pass_signing_key, settings.event_id).verify(bad)


def test_tampered_payload_and_signature(settings):
    signer = PassSigner(settings.pass_signing_key, settings.event_id)
    token = signer.issue(uuid.uuid4())
    version, payload, signature = token.split(".")
    for modified in [f"{version}.X{payload[1:]}.{signature}", token[:-5] + "AAAAA"]:
        with pytest.raises(InvalidPass):
            signer.verify(modified)


def test_other_event_or_signing_key_rejected(settings):
    token = PassSigner(settings.pass_signing_key, settings.event_id).issue(uuid.uuid4())
    for signer in [
        PassSigner(settings.pass_signing_key, "another-event"),
        PassSigner("another-key-" + "x" * 32, settings.event_id),
    ]:
        with pytest.raises(InvalidPass):
            signer.verify(token)


@pytest.mark.parametrize("format,rotate", [("PNG", 0), ("JPEG", 0), ("JPEG", 90)])
def test_actual_qr_photo_decode(settings, format, rotate):
    token = PassSigner(settings.pass_signing_key, settings.event_id).issue(uuid.uuid4())
    image = Image.open(BytesIO(make_qr(token))).convert("RGB").rotate(rotate, expand=True)
    output = BytesIO()
    image.save(output, format=format, quality=70)
    assert decode_qr(output.getvalue()) == token


def test_no_qr_and_bad_image_rejected():
    blank = BytesIO()
    Image.new("RGB", (300, 300), "white").save(blank, "PNG")
    for image in [b"not an image", blank.getvalue(), b"x" * (8 * 1024 * 1024 + 1)]:
        with pytest.raises(InvalidQR):
            decode_qr(image)


def test_multiple_qr_rejected(settings):
    signer = PassSigner(settings.pass_signing_key, settings.event_id)
    images = [
        Image.open(BytesIO(make_qr(signer.issue(uuid.uuid4())))).convert("RGB") for _ in range(2)
    ]
    canvas = Image.new("RGB", (images[0].width * 2 + 40, images[0].height), "white")
    for index, image in enumerate(images):
        canvas.paste(image, (index * (image.width + 40), 0))
    output = BytesIO()
    canvas.save(output, "PNG")
    with pytest.raises(InvalidQR, match="несколько"):
        decode_qr(output.getvalue())


def test_invalid_organizer_configuration(settings):
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            api_key=settings.api_key,
            pass_signing_key=settings.pass_signing_key,
            organizer_ids="9001,not-a-number",
        )

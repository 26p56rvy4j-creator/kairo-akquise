from __future__ import annotations

import phonenumbers


def normalize_phone_de(raw: object) -> tuple[str | None, str]:
    if raw is None or not str(raw).strip():
        return None, "missing"
    try:
        number = phonenumbers.parse(str(raw).strip(), "DE")
        if not phonenumbers.is_valid_number(number):
            return None, "invalid"
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164), "ok"
    except phonenumbers.NumberParseException:
        return None, "invalid"

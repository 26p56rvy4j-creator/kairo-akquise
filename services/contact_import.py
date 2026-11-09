import re
import unicodedata
from urllib.parse import urlparse


def normalize_label(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("_", " ").replace("/", " ")
    return re.sub(r"[^a-z0-9äöüß+-]+", " ", text).strip()


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def normalize_email(value: object) -> str:
    return clean_text(value).lower()


def normalize_domain(value: object) -> str:
    text = clean_text(value).lower()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    return (parsed.hostname or "").removeprefix("www.").strip(".")


def normalize_company_name(value: object) -> str:
    text = normalize_label(value)
    legal_forms = {
        "gmbh",
        "ug",
        "haftungsbeschränkt",
        "haftungsbeschraenkt",
        "haftungsbeschrankt",
        "ag",
        "kg",
        "gbr",
        "ohg",
        "e k",
        "ek",
        "einzelunternehmen",
    }
    words = [word for word in text.replace("&", " und ").split() if word not in legal_forms]
    return " ".join(words)

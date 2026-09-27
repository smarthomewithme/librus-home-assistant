"""Asynchroniczna warstwa dostÄ™pu do biblioteki librus-apix."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

from bs4 import BeautifulSoup, Tag

from librus_apix.client import Client, new_client
from librus_apix.exceptions import TokenError
from librus_apix.helpers import no_access_check

_LOGGER = logging.getLogger(__name__)

_ResultT = TypeVar("_ResultT")
_GRADE_VALUES = {
    f"{grade}{suffix}"
    for grade in range(1, 7)
    for suffix in ("", "+", "-")
}


_GRADE_METADATA_PREFIXES = (
    "ocena:",
    "przedmiot:",
    "kategoria:",
    "data:",
    "nauczyciel:",
    "licz do Å›redniej:",
    "licz do sredniej:",
    "waga:",
    "dodaÅ‚:",
    "dodal:",
    "obowiÄ…zek wyk. zadania:",
    "obowiazek wyk. zadania:",
)
_COMMENT_PATH_RE = re.compile(r"[\"'](/komentarz_oceny/\d+/\d+)[\#']")


def _comment_from_description(description: str) -> str:
    """WyciÄ…gnij wyÅ‚Ä…cznie treÅ›Ä‡ komentarza z tooltipu oceny.

    ``librus-apix`` skÅ‚ada pole ``desc`` z wielu metadanych (ocena, przedmiot,
    kategoria, data, nauczyciel itd.). Samo ``K`` widoczne w tabeli Librusa
    jest tylko znacznikiem, Å¼e komentarz istnieje. Tutaj zwracamy faktycznÄ…
    treÅ›Ä‡ po etykiecie ``Komentarz:`` zamiast caÅ‚ego tooltipu.
    """
    lines = [line.strip() for line in str(description or "").replace("\r", "").split("\n")]
    comment_lines: list[str] = []
    collecting = False
    for line in lines:
        if not line:
            if collecting and comment_lines:
                comment_lines.append("")
            continue
        folded = line.casefold()
        if folded.startswith("komentarz:"):
            collecting = True
            value = line.split(":", 1)[1].strip()
            if value:
                comment_lines.append(value)
            continue
        if collecting:
            if folded.startswith(_GRADE_METADATA_PREFIXES):
                break
            comment_lines.append(line)
    while comment_lines and not comment_lines[-1]:
        comment_lines.pop()
    return "\n".join(comment_lines).strip()


def _grade_comment(grade: Any) -> str:
    """ZwrÃ³Ä‡ faktycznÄ… treÅ›Ä‡ komentarza nauczyciela, nie znacznik ``K``."""
    return _comment_from_description(str(getattr(grade, "desc", "") or ""))


def _comment_path(cell: Tag) -> str:
    """ZnajdÅº adres popupu komentarza zapisany w ``onclick`` znacznika K."""
    for link in cell.find_all("a"):
        onclick = str(link.get("onclick", "") or "")
        match = _COMMENT_PATH_RE.search(onclick)
        if match:
            return match.group(1)
    return ""


def _popup_comment_text(html: str) -> str:
    """WyciÄ…gnij treÅ›Ä‡ maÅ‚ego okna ``Komentarz do oceny``.

    UkÅ‚ad popupu zmieniaÅ‚ siÄ™ miÄ™dzy wersjami Synergii, dlatego parser jest
    celowo tolerancyjny: najpierw szuka typowych kontenerÃ³w treÅ›ci, a potem
    odrzuca techniczne etykiety okna.
    """
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    candidates = []
    for selector in ("#content", "#body .container", ".container.static", ".container", "body"):
        node = soup.select_one(selector)
        if node is not None:
            candidates.append(node)
    if not candidates:
        candidates = [soup]

    ignored = {
        "komentarz do oceny",
        "komentarz oceny",
        "komentarz",
        "zamknij",
        "zamknij okno",
        "drukuj",
        "librus synergia",
    }
    for node in candidates:
        lines = []
        for raw in node.get_text("\n", strip=True).splitlines():
            line = raw.strip()
            if not line or line.casefold() in ignored:
                continue
            lines.append(line)
        if lines:
            # Popup z reguÅ‚y zawiera sam komentarz. JeÅ›li pojawi siÄ™ etykieta
            # ``Komentarz:``, zwracamy tylko to, co znajduje siÄ™ za niÄ….
            joined = "\n".join(lines)
            explicit = _comment_from_description(joined)
            return explicit or joined
    return ""


def _behavior_table_rows(html: str) -> list[dict[str, Any]]:
    """WyciÄ…gnij surowe bieÅ¼Ä…ce wpisy z tabeli ``Zachowanie`` na stronie ocen."""
    soup = no_access_check(BeautifulSoup(html, "lxml"))
    wrapper = soup.find("tr", id="przedmioty_zachowanie")
    if wrapper is None:
        return []
    table = wrapper.find("table")
    if table is None:
        return []

    header = table.find("thead")
    headers = [cell.get_text(" ", strip=True).casefold() for cell in header.find_all(["td", "th"])] if header else []
    index: dict[str, int] = {}
    for i, label in enumerate(headers):
        if "kategoria" in label:
            index["category"] = i
        elif "komentarz" in label:
            index["comment"] = i
        elif label.startswith("ocen"):
            index["grade"] = i
        elif "data" in label:
            index["date"] = i
        elif "doda" in label or "nauczyciel" in label:
            index["teacher"] = i

    if not {"category", "comment", "grade", "date", "teacher"}.issubset(index):
        return []

    result: list[dict[str, Any]] = []
    semester = 0
    for row in table.find_all("tr"):
        text = row.get_text(" ", strip=True)
        match_semester = re.search(r"Okres\s+([12])", text, flags=re.IGNORECASE)
        if match_semester:
            semester = int(match_semester.group(1))
            continue
        cells = row.find_all("td", recursive=False)
        if len(cells) <= max(index.values()):
            continue

        category = cells[index["category"]].get_text(" ", strip=True)
        marker_cell = cells[index["comment"]]
        marker = marker_cell.get_text(" ", strip=True)
        grade = cells[index["grade"]].get_text(" ", strip=True)
        date_value = cells[index["date"]].get_text(" ", strip=True)
        teacher = cells[index["teacher"]].get_text(" ", strip=True)

        # Wiersze podsumowaÅ„ (punkty startowe, suma, przewidywana ocena...)
        # nie sÄ… wpisami zachowania i zwykle nie majÄ… daty w formacie ISO.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
            continue
        if not grade and not category:
            continue

        tooltip_comment = ""
        for anchor in row.find_all("a", title=True):
            tooltip_comment = _comment_from_description(str(anchor.get("title", "") or ""))
            if tooltip_comment:
                break

        result.append(
            {
                "subject": "Zachowanie",
                "grade": grade,
                "date": date_value,
                "category": category,
                "comment": tooltip_comment,
                "comment_marker": marker,
                "comment_path": _comment_path(marker_cell),
                "has_comment": bool(marker or tooltip_comment),
                "teacher": teacher,
                "semester": semester or current_semester(),
                "type": "behavior_current",
            }
        )
    return result


def current_semester(today: date | None = None) -> int:
    """ZwrÃ³Ä‡ semestr odpowiadajÄ…cy bieÅ¼Ä…cej czÄ™Å›ci roku szkolnego."""
    month = (today or date.today()).month
    return 1 if month >= 9 else 2


def _is_grade(value: str) -> bool:
    """SprawdÅº, czy opisowa pozycja jest zwykÅ‚Ä… ocenÄ… liczbowÄ…²È="25åÌôÌÀ¤¤¹ÍÑÉ™Ñ¥µ” ˆ•d´•´´•ˆ¤°(€€€€€€€€€€€€¤((€€€€€€€É•ÑÕÉ¸…Ý…¥ÐÍ•±˜¹}…±±}Ý¥Ñ¡}É•ÑÉä ‰é…‘‡ˆ°É•ÅÕ•ÍÐ¤((€€€…Íå¹Œ‘•˜…Íå¹}•Ñ}…ÑÑ•¹‘…¹”¡Í•±˜¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°¹åutð9½¹”è(€€€€€€€€ˆˆ‰A½‰¥•ÉèÝÁ¥Íä™É•­Ý•¹©¤é”ÝÍéåÍÑ­¥ éÝËÍ½¹å Í•µ•ÍÑËÍÜ¸ˆˆˆ((€€€€€€€…Íå¹Œ‘•˜É•ÅÕ•ÍÐ ¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°¹åutè(€€€€€€€€€€€™É½´±¥‰ÉÕÍ}…Á¥à¹…ÑÑ•¹‘…¹”¥µÁ½ÉÐ•Ñ}…ÑÑ•¹‘…¹”((€€€€€€€€€€€Í•µ•ÍÑ•ÉÌ€ô…Ý…¥ÐÍ•±˜¹}ÉÕ¹}‰±½­¥¹œ (€€€€€€€€€€€€€€€•Ñ}…ÑÑ•¹‘…¹”°Í•±˜¹}±¥•¹Ð(€€€€€€€€€€€€¤(€€€€€€€€€€€É•ÍÕ±Ðè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€€€€€€€€€™½ÈÍ•µ•ÍÑ•È¥¸Í•µ•ÍÑ•ÉÌ½Èmtè(€€€€€€€€€€€€€€€™½È•¹ÑÉä¥¸Í•µ•ÍÑ•È½Èmtè(€€€€€€€€€€€€€€€€€€€É•ÍÕ±Ð¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Íåµ‰½°ˆèÍÑÈ¡•Ñ…ÑÑÈ¡•¹ÑÉä°€‰Íåµ‰½°ˆ°€ˆˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÑåÀˆèÍÑÈ¡•Ñ…ÑÑÈ¡•¹ÑÉä°€‰ÑåÁ”ˆ°€ˆˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‘…Ñ„ˆèÍÑÈ¡•Ñ…ÑÑÈ¡•¹ÑÉä°€‰‘…Ñ”ˆ°€ˆˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÁÉé•‘µ¥½ÐˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€•Ñ…ÑÑÈ¡•¹ÑÉä°€‰ÍÕ‰©•Ðˆ°€ˆˆ¤½È€ˆˆ(€€€€€€€€€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰¹…Õéå¥•°ˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€•Ñ…ÑÑÈ¡•¹ÑÉä°€‰Ñ•…¡•Èˆ°€ˆˆ¤½È€ˆˆ(€€€€€€€€€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰½‘é¥¹„ˆè•Ñ…ÑÑÈ¡•¹ÑÉä°€‰Á•É¥½ˆ°9½¹”¤°(€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð((€€€€€€€É•ÑÕÉ¸…Ý…¥ÐÍ•±˜¹}…±±}Ý¥Ñ¡}É•ÑÉä ‰™É•­Ý•¹©¤ˆ°É•ÅÕ•ÍÐ¤((€€€…Íå¹Œ‘•˜…Íå¹}•Ñ}…¹¹½Õ¹•µ•¹ÑÌ¡Í•±˜¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°ÍÑÉutð9½¹”è(€€€€€€€€ˆˆ‰A½‰¥•Éè½Ÿ	½Íé•¹¥„Íé­½±¹”¸ˆˆˆ((€€€€€€€…Íå¹Œ‘•˜É•ÅÕ•ÍÐ ¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°ÍÑÉutè(€€€€€€€€€€€™É½´±¥‰ÉÕÍ}…Á¥à¹…¹¹½Õ¹•µ•¹ÑÌ¥µÁ½ÉÐ•Ñ}…¹¹½Õ¹•µ•¹ÑÌ((€€€€€€€€€€€…¹¹½Õ¹•µ•¹ÑÌ€ô…Ý…¥ÐÍ•±˜¹}ÉÕ¹}‰±½­¥¹œ (€€€€€€€€€€€€€€€•Ñ}…¹¹½Õ¹•µ•¹ÑÌ°Í•±˜¹}±¥•¹Ð(€€€€€€€€€€€€¤(€€€€€€€€€€€É•ÑÕÉ¸l(€€€€€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€€€€€‰ÑåÑÕ°ˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€•Ñ…ÑÑÈ¡…¹¹½Õ¹•µ•¹Ð°€‰Ñ¥Ñ±”ˆ°€ˆˆ¤½È€ˆˆ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€‰¹…‘…Ý„ˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€•Ñ…ÑÑÈ¡…¹¹½Õ¹•µ•¹Ð°€‰…ÕÑ¡½Èˆ°€ˆˆ¤½È€ˆˆ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€‰½Á¥ÌˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€•Ñ…ÑÑÈ¡…¹¹½Õ¹•µ•¹Ð°€‰‘•ÍÉ¥ÁÑ¥½¸ˆ°€ˆˆ¤½È€ˆˆ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€€‰‘…Ñ„ˆèÍÑÈ (€€€€€€€€€€€€€€€€€€€€€€€•Ñ…ÑÑÈ¡…¹¹½Õ¹•µ•¹Ð°€‰‘…Ñ”ˆ°€ˆˆ¤½È€ˆˆ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€™½È…¹¹½Õ¹•µ•¹Ð¥¸…¹¹½Õ¹•µ•¹ÑÌ½Èmt(€€€€€€€€€€€t((€€€€€€€É•ÑÕÉ¸…Ý…¥ÐÍ•±˜¹}…±±}Ý¥Ñ¡}É•ÑÉä ‰½Ÿ	½Íé—ˆ°É•ÅÕ•ÍÐ¤((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}™•Ñ¡}Í¡•‘Õ±”¡±¥•¹Ðè±¥•¹Ð°Ñ½‘…äè‘…Ñ”¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°¹åutè(€€€€€€€€ˆˆ‰A½‰¥•Éè‰¥—óä¤¹…ÍÓeÁ¹äµ¥•Í§ŒÑ•Éµ¥¹…Éé„¸ˆˆˆ(€€€€€€€™É½´±¥‰ÉÕÍ}…Á¥à¹Í¡•‘Õ±”¥µÁ½ÉÐ•Ñ}Í¡•‘Õ±”((€€€€€€€µ½¹Ñ¡Ì€ôl(€€€€€€€€€€€€¡Ñ½‘…ä¹å•…È°Ñ½‘…ä¹µ½¹Ñ ¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€Ñ½‘…ä¹å•…È€¬€Ä¥˜Ñ½‘…ä¹µ½¹Ñ €ôô€ÄÈ•±Í”Ñ½‘…ä¹å•…È°(€€€€€€€€€€€€€€€€Ä¥˜Ñ½‘…ä¹µ½¹Ñ €ôô€ÄÈ•±Í”Ñ½‘…ä¹µ½¹Ñ €¬€Ä°(€€€€€€€€€€€€¤°(€€€€€€€t(€€€€€€€•Ù•¹ÑÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€€€€€™½Èå•…È°µ½¹Ñ ¥¸µ½¹Ñ¡Ìè(€€€€€€€€€€€µ½¹Ñ¡±ä€ô•Ñ}Í¡•‘Õ±”¡±¥•¹Ð°˜‰íµ½¹Ñ èÀÉ‘ôˆ°ÍÑÈ¡å•…È¤¤½Èíô(€€€€€€€€€€€™½È‘…å}¹Õµ‰•È°‘…å}•Ù•¹ÑÌ¥¸µ½¹Ñ¡±ä¹¥Ñ•µÌ ¤è(€€€€€€€€€€€€€€€•Ù•¹Ñ}‘…Ñ”€ô‘…Ñ”¡å•…È°µ½¹Ñ °¥¹Ð¡‘…å}¹Õµ‰•È¤¤(€€€€€€€€€€€€€€€¥˜•Ù•¹Ñ}‘…Ñ”€ðÑ½‘…äè(€€€€€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€€€€€™½È•Ù•¹Ð¥¸‘…å}•Ù•¹ÑÌè(€€€€€€€€€€€€€€€€€€€•Ù•¹ÑÌ¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‘…Ñ„ˆè•Ù•¹Ñ}‘…Ñ”¹¥Í½™½Éµ…Ð ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Ñå‘é¥•¸ˆè•Ù•¹Ñ}‘…Ñ”¹ÍÑÉ™Ñ¥µ” ˆ•ˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÑåÑÕ°ˆè•Ù•¹Ð¹Ñ¥Ñ±”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÁÉé•‘µ¥½Ðˆè•Ù•¹Ð¹ÍÕ‰©•Ð°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰½‘é¥¹„ˆè•Ù•¹Ð¹¡½ÕÈ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰¹Õµ•É}±•­©¤ˆè•Ù•¹Ð¹¹Õµ‰•È°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Íéé•½±äˆè•Ù•¹Ð¹‘…Ñ„°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰¡É•˜ˆè•Ù•¹Ð¹¡É•˜°(€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸Í½ÉÑ•¡•Ù•¹ÑÌ°­•äõ±…µ‰‘„¥Ñ•´è¥Ñ•µl‰‘…Ñ„‰t¤((€€€…Íå¹Œ‘•˜…Íå¹}•Ñ}Í¡•‘Õ±”¡Í•±˜¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°¹åutð9½¹”è(€€€€€€€€ˆˆ‰A½‰¥•Éè¹…‘¡½‘ë”ÝÁ¥ÍäÑ•Éµ¥¹…Éé„¸ˆˆˆ((€€€€€€€…Íå¹Œ‘•˜É•ÅÕ•ÍÐ ¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°¹åutè(€€€€€€€€€€€É•ÑÕÉ¸…Ý…¥ÐÍ•±˜¹}ÉÕ¹}‰±½­¥¹œ (€€€€€€€€€€€€€€€Í•±˜¹}™•Ñ¡}Í¡•‘Õ±”°Í•±˜¹}±¥•¹Ð°‘…Ñ”¹Ñ½‘…ä ¤(€€€€€€€€€€€€¤((€€€€€€€É•ÑÕÉ¸…Ý…¥ÐÍ•±˜¹}…±±}Ý¥Ñ¡}É•ÑÉä ‰Ñ•Éµ¥¹…Éé„ˆ°É•ÅÕ•ÍÐ¤((€€€…Íå¹Œ‘•˜…Íå¹}•Ñ}Í¡•‘Õ±•}½¹Ñ•¹Ð (€€€€€€€Í•±˜°¡É•˜èÍÑÈ(€€€€¤€´ø‘¥ÑmÍÑÈ°ÍÑÉtð9½¹”è(€€€€€€€€ˆˆ‰A½‰¥•ÉèÁ—	¹”Íéé•ŸÏ	ä©•‘¹•¼ÝÁ¥ÍÔÑ•Éµ¥¹…Éé„¸ˆˆˆ(€€€€€€€¥˜¹½Ð¡É•˜½È€ˆ¼ˆ¹½Ð¥¸¡É•˜è(€€€€€€€€€€€É•ÑÕÉ¸9½¹”((€€€€€€€ÁÉ•™¥à°‘•Ñ…¥±}ÕÉ°€ô¡É•˜¹ÍÁ±¥Ð ˆ¼ˆ°€Ä¤(€€€€€€€¥˜¹½ÐÁÉ•™¥à½È¹½Ð‘•Ñ…¥±}ÕÉ°è(€€€€€€€€€€€É•ÑÕÉ¸9½¹”((€€€€€€€…Íå¹Œ‘•˜É•ÅÕ•ÍÐ ¤€´ø‘¥ÑmÍÑÈ°ÍÑÉtð9½¹”è(€€€€€€€€€€€™É½´±¥‰ÉÕÍ}…Á¥à¹Í¡•‘Õ±”¥µÁ½ÉÐÍ¡•‘Õ±•}‘•Ñ…¥°((€€€€€€€€€€€‘•Ñ…¥±Ì€ô…Ý…¥ÐÍ•±˜¹}ÉÕ¹}‰±½­¥¹œ (€€€€€€€€€€€€€€€Í¡•‘Õ±•}‘•Ñ…¥°°Í•±˜¹}±¥•¹Ð°ÁÉ•™¥à°‘•Ñ…¥±}ÕÉ°(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜‘•Ñ…¥±Ì¥Ì9½¹”è(€€€€€€€€€€€€€€€É•ÑÕÉ¸9½¹”(€€€€€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€€€€ÍÑÈ¡­•ä¤¹ÍÑÉ¥À ¤èÍÑÈ¡Ù…±Õ”½È€ˆˆ¤¹ÍÑÉ¥À ¤(€€€€€€€€€€€€€€€™½È­•ä°Ù…±Õ”¥¸‘•Ñ…¥±Ì¹¥Ñ•µÌ ¤(€€€€€€€€€€€ô((€€€€€€€É•ÑÕÉ¸…Ý…¥ÐÍ•±˜¹}…±±}Ý¥Ñ¡}É•ÑÉä ‰ÑÉ—m¤ÝÁ¥ÍÔÑ•Éµ¥¹…Éé„ˆ°É•ÅÕ•ÍÐ¤((€€€…Íå¹Œ‘•˜…Íå¹}•Ñ}ÍÑÕ‘•¹Ñ}¥¹™½Éµ…Ñ¥½¸¡Í•±˜¤€´ø¹äð9½¹”è(€€€€€€€€ˆˆ‰A½‰¥•ÉèÁ½‘ÍÑ…Ý½Ý”¥¹™½Éµ…©”¼Õé¹¥Ô¸ˆˆˆ((€€€€€€€…Íå¹Œ‘•˜É•ÅÕ•ÍÐ ¤€´ø¹äè(€€€€€€€€€€€™É½´±¥‰ÉÕÍ}…Á¥à¹ÍÑÕ‘•¹Ñ}¥¹™½Éµ…Ñ¥½¸¥µÁ½ÉÐ•Ñ}ÍÑÕ‘•¹Ñ}¥¹™½Éµ…Ñ¥½¸((€€€€€€€€€€€É•ÑÕÉ¸…Ý…¥ÐÍ•±˜¹}ÉÕ¹}‰±½­¥¹œ (€€€€€€€€€€€€€€€•Ñ}ÍÑÕ‘•¹Ñ}¥¹™½Éµ…Ñ¥½¸°Í•±˜¹}±¥•¹Ð(€€€€€€€€€€€€¤((€€€€€€€É•ÑÕÉ¸…Ý…¥ÐÍ•±˜¹}…±±}Ý¥Ñ¡}É•ÑÉä ‰¥¹™½Éµ…©¤¼Õé¹¥Ôˆ°É•ÅÕ•ÍÐ¤((€€€…Íå¹Œ‘•˜…Íå¹}•Ñ}Ñ¥µ•Ñ…‰±” (€€€€€€€Í•±˜°µ½¹‘…å}‘…Ñ•Ìè%Ñ•É…‰±•m‘…Ñ•t(€€€€¤€´ø‘¥ÑmÍÑÈ°¹åtð9½¹”è(€€€€€€€€ˆˆ‰A½‰¥•ÉèÁ±…¸‘±„Á½‘…¹å Ñå½‘¹¤¸ˆˆˆ(€€€€€€€µ½¹‘…åÌ€ôÑÕÁ±”¡µ½¹‘…å}‘…Ñ•Ì¤((€€€€€€€…Íå¹Œ‘•˜É•ÅÕ•ÍÐ ¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€€€€€™É½´±¥‰ÉÕÍ}…Á¥à¹Ñ¥µ•Ñ…‰±”¥µÁ½ÉÐ•Ñ}Ñ¥µ•Ñ…‰±”((€€€€€€€€€€€Ý••­Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€€€€€€€€€•ÉÉ½ÉÌè±¥ÍÑm‘¥ÑmÍÑÈ°ÍÑÉut€ômt((€€€€€€€€€€€™½Èµ½¹‘…ä¥¸µ½¹‘…åÌè(€€€€€€€€€€€€€€€µ½¹‘…å}‘…Ñ•Ñ¥µ”€ô‘…Ñ•Ñ¥µ”¹½µ‰¥¹”¡µ½¹‘…ä°‘…Ñ•Ñ¥µ”¹µ¥¸¹Ñ¥µ” ¤¤(€€€€€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€€€€€‘…åÌ€ô…Ý…¥ÐÍ•±˜¹}ÉÕ¹}‰±½­¥¹œ (€€€€€€€€€€€€€€€€€€€€€€€•Ñ}Ñ¥µ•Ñ…‰±”°Í•±˜¹}±¥•¹Ð°µ½¹‘…å}‘…Ñ•Ñ¥µ”(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€•á•ÁÐQ½­•¹ÉÉ½Èè(€€€€€€€€€€€€€€€€€€€É…¥Í”(€€€€€€€€€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•ÉÈè(€€€€€€€€€€€€€€€€€€€‘¥…¹½ÍÑ¥Œ€ôì(€€€€€€€€€€€€€€€€€€€€€€€€‰Ý••­}ÍÑ…ÉÐˆèµ½¹‘…ä¹¥Í½™½Éµ…Ð ¤°(€€€€€€€€€€€€€€€€€€€€€€€€‰•ÉÉ½É}ÑåÁ”ˆèÑåÁ”¡•ÉÈ¤¹}}¹…µ•}|°(€€€€€€€€€€€€€€€€€€€€€€€€‰•ÉÉ½ÈˆèÍÑÈ¡•ÉÈ¤°(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€•ÉÉ½ÉÌ¹…ÁÁ•¹¡‘¥…¹½ÍÑ¥Œ¤(€€€€€€€€€€€€€€€€€€€}1=H¹Ý…É¹¥¹œ (€€€€€€€€€€€€€€€€€€€€€€€€‰9¥”Õ‘‡	¼Í§dÁ½‰É‡Á±…¹Ô‘±„Ñå½‘¹¥„€•Ìè€•Ìˆ°(€€€€€€€€€€€€€€€€€€€€€€€µ½¹‘…ä¹¥Í½™½Éµ…Ð ¤°(€€€€€€€€€€€€€€€€€€€€€€€•ÉÈ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”((€€€€€€€€€€€€€€€Ý••­Ì¹…ÁÁ•¹¡ì‰Ý••­}ÍÑ…ÉÐˆèµ½¹‘…ä¹¥Í½™½Éµ…Ð ¤°€‰‘…åÌˆè‘…åÍô¤((€€€€€€€€€€€¥˜¹½ÐÝ••­Ìè(€€€€€€€€€€€€€€€‘•Ñ…¥±Ì€ô€ˆì€ˆ¹©½¥¸ (€€€€€€€€€€€€€€€€€€€˜‰í¥Ñ•µlÝ••­}ÍÑ…ÉÐuôèí¥Ñ•µl•ÉÉ½É}ÑåÁ”uôèí¥Ñ•µl•ÉÉ½Èuôˆ(€€€€€€€€€€€€€€€€€€€™½È¥Ñ•´¥¸•ÉÉ½ÉÌ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€€‰1¥‰ÉÕÌ¹¥”éÝËÍ§Á±…¹Ô‘±„ÍÁÉ…Ý‘é…¹å Ñå½‘¹¤ˆ(€€€€€€€€€€€€€€€€€€€˜ˆ€¡í‘•Ñ…¥±Ì½È€‰É…¬Íéé•ŸÏÍÜô¤ˆ(€€€€€€€€€€€€€€€€¤((€€€€€€€€€€€É•ÑÕÉ¸ì‰Ý••­ÌˆèÝ••­Ì°€‰Ý••­}•ÉÉ½ÉÌˆè•ÉÉ½ÉÍô((€€€€€€€É•ÑÕÉ¸…Ý…¥ÐÍ•±˜¹}…±±}Ý¥Ñ¡}É•ÑÉä (€€€€€€€€€€€€‰Á±…¹Ô±•­©¤ˆ°É•ÅÕ•ÍÐ°É…¥Í•}½¹}™…¥±ÕÉ”õQÉÕ”(€€€€€€€€¤(
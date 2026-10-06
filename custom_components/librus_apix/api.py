"""Asynchroniczna warstwa dostępu do biblioteki librus-apix."""

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
    "licz do średniej:",
    "licz do sredniej:",
    "waga:",
    "dodał:",
    "dodal:",
    "obowiązek wyk. zadania:",
    "obowiazek wyk. zadania:",
)
_COMMENT_PATH_RE = re.compile(r"[\"'](/komentarz_oceny/\d+/\d+)[\"']")


def _comment_from_description(description: str) -> str:
    """Wyciągnij wyłącznie treść komentarza z tooltipu oceny.

    ``librus-apix`` składa pole ``desc`` z wielu metadanych (ocena, przedmiot,
    kategoria, data, nauczyciel itd.). Samo ``K`` widoczne w tabeli Librusa
    jest tylko znacznikiem, że komentarz istnieje. Tutaj zwracamy faktyczną
    treść po etykiecie ``Komentarz:`` zamiast całego tooltipu.
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
    """Zwróć faktyczną treść komentarza nauczyciela, nie znacznik ``K``."""
    return _comment_from_description(str(getattr(grade, "desc", "") or ""))


def _grade_metadata_value(description: str, *labels: str) -> str:
    """Odczytaj pojedynczą wartość z metadanych tooltipu oceny."""
    wanted = tuple(label.casefold().rstrip(":") for label in labels)
    for line in str(description or "").replace("\r", "").split("\n"):
        raw = line.strip()
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        if key.strip().casefold() in wanted:
            return value.strip()
    return ""


def _grade_weight(grade: Any) -> float | None:
    """Odczytaj wagę z obiektu librus-apix, z fallbackiem do tooltipu."""
    direct = getattr(grade, "weight", None)
    if direct not in (None, ""):
        try:
            value = float(direct)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value

    description = str(getattr(grade, "desc", "") or "")
    raw = _grade_metadata_value(description, "waga", "weight").replace(",", ".")
    if not raw:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    if not match:
        return None
    try:
        value = float(match.group(0))
    except ValueError:
        return None
    return value if value > 0 else None


def _grade_counts_to_average(grade: Any) -> bool | None:
    """Zwróć flagę counts librus-apix, z fallbackiem do tooltipu."""
    direct = getattr(grade, "counts", None)
    if isinstance(direct, bool):
        return direct
    if direct not in (None, ""):
        normalized = str(direct).strip().casefold()
        if normalized in {"tak", "yes", "true", "1"}:
            return True
        if normalized in {"nie", "no", "false", "0"}:
            return False

    description = str(getattr(grade, "desc", "") or "")
    raw = _grade_metadata_value(
        description,
        "licz do średniej",
        "licz do sredniej",
        "counts toward average",
    ).casefold()
    if not raw:
        return None
    if raw in {"tak", "yes", "true", "1"}:
        return True
    if raw in {"nie", "no", "false", "0"}:
        return False
    return None


def _comment_path(cell: Tag) -> str:
    """Znajdź adres popupu komentarza zapisany w ``onclick`` znacznika K."""
    for link in cell.find_all("a"):
        onclick = str(link.get("onclick", "") or "")
        match = _COMMENT_PATH_RE.search(onclick)
        if match:
            return match.group(1)
    return ""


def _popup_comment_text(html: str) -> str:
    """Wyciągnij treść małego okna ``Komentarz do oceny``.

    Układ popupu zmieniał się między wersjami Synergii, dlatego parser jest
    celowo tolerancyjny: najpierw szuka typowych kontenerów treści, a potem
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
            # Popup z reguły zawiera sam komentarz. Jeśli pojawi się etykieta
            # ``Komentarz:``, zwracamy tylko to, co znajduje się za nią.
            joined = "\n".join(lines)
            explicit = _comment_from_description(joined)
            return explicit or joined
    return ""


def _behavior_table_rows(html: str) -> list[dict[str, Any]]:
    """Wyciągnij surowe bieżące wpisy z tabeli ``Zachowanie`` na stronie ocen."""
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

        # Wiersze podsumowań (punkty startowe, suma, przewidywana ocena...)
        # nie są wpisami zachowania i zwykle nie mają daty w formacie ISO.
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
    """Zwróć semestr odpowiadający bieżącej części roku szkolnego."""
    month = (today or date.today()).month
    return 1 if month >= 9 else 2


def _is_grade(value: str) -> bool:
    """Sprawdź, czy opisowa pozycja jest zwykłą oceną liczbową."""
    return value.strip() in _GRADE_VALUES


def _is_behavior_subject(value: str) -> bool:
    """Rozpoznaj sekcję zachowania w ocenach opisowych Librusa."""
    normalized = str(value or "").strip().casefold()
    return any(token in normalized for token in ("zachow", "behaviour", "behavior", "conduct"))


_POLISH_FOLD = str.maketrans(
    {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
    }
)


def _normalized_label(value: str) -> str:
    """Uprość etykietę tabeli Librusa bez utraty semantyki pola."""
    text = str(value or "").casefold().translate(_POLISH_FOLD)
    return " ".join(text.replace(":", " ").split())


def _header_row(table: Tag) -> tuple[list[Tag], Tag | None]:
    """Zwróć komórki nagłówka i wiersz, aby później go pominąć."""
    thead = table.find("thead")
    if thead is not None:
        row = thead.find("tr")
        if row is not None:
            cells = row.find_all(["th", "td"], recursive=False)
            if cells:
                return cells, row
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if cells and any(cell.name == "th" for cell in cells):
            return cells, row
    return [], None


def _note_table_rows(html: str) -> list[dict[str, str]]:
    """Parsuj stronę ``Uczeń → Uwagi``.

    Aktualny układ Synergii ma kolumny: Uwaga, Data, Kto dodał, Rodzaj
    uwagi i Kategoria. Parser akceptuje też starsze warianty nazw kolumn, ale
    przy nieznanym układzie kończy błędem zamiast udawać pustą listę.
    """
    soup = no_access_check(BeautifulSoup(html or "", "lxml"))
    page_text = _normalized_label(soup.get_text(" ", strip=True))
    if "brak uwag" in page_text:
        return []

    field_aliases = {
        "uwaga": "content",
        "tresc": "content",
        "tresc uwagi": "content",
        "data": "date",
        "kto dodal": "author",
        "dodal": "author",
        "nauczyciel": "author",
        "rodzaj uwagi": "type",
        "rodzaj": "type",
        "kategoria": "category",
    }

    for table in soup.find_all("table"):
        header_cells, header_row = _header_row(table)
        if not header_cells:
            # W części kont nagłówki są zwykłymi td w pierwszym wierszu.
            first = table.find("tr")
            candidate = first.find_all(["th", "td"], recursive=False) if first else []
            candidate_labels = [_normalized_label(cell.get_text(" ", strip=True)) for cell in candidate]
            if "uwaga" in candidate_labels and "data" in candidate_labels:
                header_cells, header_row = candidate, first
        if not header_cells:
            continue

        fields = [
            field_aliases.get(_normalized_label(cell.get_text(" ", strip=True)))
            for cell in header_cells
        ]
        if "content" not in fields or "date" not in fields:
            continue

        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            if row is header_row or row.find_parent("table") is not table:
                continue
            cells = row.find_all("td", recursive=False)
            if len(cells) < len(fields):
                continue
            record = {
                field: cells[index].get_text(" ", strip=True)
                for index, field in enumerate(fields)
                if field is not None
            }
            if record.get("content") or record.get("date"):
                rows.append(
                    {
                        "content": record.get("content", ""),
                        "date": record.get("date", ""),
                        "author": record.get("author", ""),
                        "type": record.get("type", ""),
                        "category": record.get("category", ""),
                    }
                )
        if rows:
            return rows

    raise ValueError("Nie rozpoznano układu strony Uwagi")


def _safe_attribute_key(label: str, index: int) -> str:
    """Zbuduj stabilny klucz atrybutu dla nieudokumentowanej tabeli osiągnięć."""
    normalized = _normalized_label(label)
    key = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return key or f"pole_{index + 1}"


def _achievement_table_rows(html: str) -> list[dict[str, str]]:
    """Parsuj ``Szczególne osiągnięcia ucznia``.

    Na koncie bez wpisów Synergia pokazuje jawny komunikat
    ``Brak szczególnych osiągnięć``. Dla przyszłych wpisów obsługujemy typowe
    dla Synergii tabele kolumnowe oraz tabele par etykieta→wartość. Jeżeli
    Librus zastosuje inny układ, zwracamy błąd i koordynator zachowa cache.
    """
    soup = no_access_check(BeautifulSoup(html or "", "lxml"))
    page_text = _normalized_label(soup.get_text(" ", strip=True))
    if "brak szczegolnych osiagniec" in page_text:
        return []

    for table in soup.select("table.decorated, table.decoratedTable"):
        header_cells, header_row = _header_row(table)
        if header_cells:
            keys = [
                _safe_attribute_key(cell.get_text(" ", strip=True), index)
                for index, cell in enumerate(header_cells)
            ]
            rows: list[dict[str, str]] = []
            for row in table.find_all("tr"):
                if row is header_row or row.find_parent("table") is not table:
                    continue
                cells = row.find_all("td", recursive=False)
                if len(cells) < len(keys):
                    continue
                record = {
                    key: cells[index].get_text(" ", strip=True)
                    for index, key in enumerate(keys)
                }
                if any(record.values()):
                    rows.append(record)
            if rows:
                return rows

        # Starsze ekrany Synergii często zapisują pojedynczy wpis jako
        # kolejne wiersze ``etykieta | wartość``.
        pairs: dict[str, str] = {}
        for index, row in enumerate(table.find_all("tr")):
            if row.find_parent("table") is not table:
                continue
            cells = row.find_all("td", recursive=False)
            if len(cells) != 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            if label and value:
                pairs[_safe_attribute_key(label, index)] = value
        if pairs:
            return [pairs]

    raise ValueError("Nie rozpoznano układu strony Szczególne osiągnięcia")


class LibrusApiClient:
    """Udostępnij nieblokujące metody używane przez koordynatory HA."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._client: Client | None = None
        self._token: Any | None = None
        self._auth_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._auth_retry_after = 0.0

    async def _run_blocking(self, function: Callable[..., _ResultT], *args: Any) -> _ResultT:
        """Wykonaj blokujące wywołanie biblioteki poza pętlą HA."""
        async with self._request_lock:
            return await asyncio.get_running_loop().run_in_executor(
                None, function, *args
            )

    def _reset_authentication(self) -> None:
        self._client = None
        self._token = None
        self._auth_retry_after = 0.0

    async def async_authenticate(self) -> bool:
        """Utwórz sesję i pobierz token Librusa."""
        loop = asyncio.get_running_loop()
        if loop.time() < self._auth_retry_after:
            return False

        async with self._auth_lock:
            if self._client is not None and self._token is not None:
                return True
            if loop.time() < self._auth_retry_after:
                return False
            try:
                client = await self._run_blocking(new_client)
                token = await self._run_blocking(
                    client.get_token, self.username, self.password
                )
                if not token:
                    raise ValueError("Librus nie zwrócił tokenu logowania")
            except Exception as err:  # biblioteka zgłasza kilka typów błędów sieci
                self._reset_authentication()
                self._auth_retry_after = loop.time() + 30
                _LOGGER.warning("Logowanie do Librusa nie powiodło się: %s", err)
                return False

            self._client = client
            self._token = token
            self._auth_retry_after = 0.0
            return True

    async def _call_with_retry(
        self,
        label: str,
        operation: Callable[[], Awaitable[_ResultT]],
        *,
        raise_on_failure: bool = False,
    ) -> _ResultT | None:
        """Ponów wywołanie raz po wygaśnięciu sesji lub błędzie połączenia."""
        for attempt in range(2):
            if not await self.async_authenticate():
                if raise_on_failure:
                    raise RuntimeError("Nie udało się zalogować do Librusa")
                return None

            try:
                return await operation()
            except TokenError as err:
                self._reset_authentication()
                if attempt == 0:
                    _LOGGER.info("Sesja Librusa wygasła; ponawiam: %s", label)
                    continue
                if raise_on_failure:
                    raise RuntimeError(f"Nie udało się pobrać: {label}") from err
                _LOGGER.warning("Nie udało się pobrać %s po ponownym logowaniu", label)
                return None
            except Exception as err:  # odpowiedzi librus-apix nie mają wspólnej bazy
                self._reset_authentication()
                if attempt == 0:
                    _LOGGER.warning("Błąd pobierania %s; ponawiam: %s", label, err)
                    continue
                _LOGGER.exception("Nie udało się pobrać: %s", label)
                if raise_on_failure:
                    raise RuntimeError(f"Nie udało się pobrać: {label}") from err
                return None

        return None

    async def async_get_grades(self) -> list[dict[str, Any]] | None:
        """Pobierz oceny z bieżącego semestru."""

        async def request() -> list[dict[str, Any]]:
            from librus_apix.grades import get_grades

            numeric, _, descriptive = await self._run_blocking(
                get_grades, self._client, "all"
            )
            semester = current_semester()
            result: list[dict[str, Any]] = []

            for subject_grades in numeric or []:
                for subject, grades in subject_grades.items():
                    for grade in grades:
                        if grade.semester != semester:
                            continue
                        # Librus pokazuje bieżące wpisy zachowania w tej samej
                        # tabeli co zwykłe oceny i potrafi zwracać własne kody
                        # (np. ``3bb``). Nie próbujemy ich interpretować ani
                        # traktować jak oceny liczbowe — zachowujemy wartość 1:1.
                        entry_type = (
                            "behavior_current"
                            if _is_behavior_subject(subject)
                            else "numeric"
                        )
                        result.append(
                            {
                                "subject": subject,
                                "grade": grade.grade,
                                "date": grade.date,
                                "category": grade.category,
                                "comment": _grade_comment(grade),
                                "teacher": getattr(grade, "teacher", ""),
                                "semester": grade.semester,
                                "weight": _grade_weight(grade),
                                "counts_to_average": _grade_counts_to_average(grade),
                                "type": entry_type,
                            }
                        )

            for subject_grades in descriptive or []:
                for subject, grades in subject_grades.items():
                    for grade in grades:
                        value = str(grade.grade or "").strip()
                        if grade.semester != semester:
                            continue
                        description = str(getattr(grade, "desc", "") or "")
                        title = str(getattr(grade, "title", "") or subject or "").strip()

                        # Zachowanie w Librusie jest zwracane przez librus-apix
                        # wśród ocen opisowych. Wartości takie jak „wzorowe”
                        # nie przechodzą filtra zwykłych ocen 1–6, dlatego
                        # zachowujemy je jako osobny typ rekordu.
                        if _is_behavior_subject(subject) or _is_behavior_subject(title):
                            result.append(
                                {
                                    "subject": subject or title or "Zachowanie",
                                    "grade": value,
                                    "date": str(getattr(grade, "date", "") or ""),
                                    "category": title if title != subject else "",
                                    "comment": description,
                                    "teacher": getattr(grade, "teacher", ""),
                                    "semester": grade.semester,
                                    "type": "behavior",
                                }
                            )
                            continue

                        if not _is_grade(value):
                            continue

                        result.append(
                            {
                                "subject": subject,
                                "grade": value,
                                "date": grade.date,
                                "category": description.splitlines()[0]
                                if description
                                else "",
                                "comment": description,
                                "teacher": getattr(grade, "teacher", ""),
                                "semester": grade.semester,
                                "type": "descriptive",
                            }
                        )

            return result

        return await self._call_with_retry("ocen", request)

    async def async_get_current_behavior(self) -> list[dict[str, Any]] | None:
        """Pobierz bieżące wpisy z dedykowanej tabeli ``Zachowanie``.

        ``librus-apix`` nie mapuje tej tabeli w stabilny sposób, dlatego
        czytamy ją bezpośrednio ze strony ocen. Znacznik ``K`` jest tylko
        informacją o istnieniu komentarza; jeśli występuje, otwieramy ten sam
        endpoint komentarza co interfejs Librusa i zapisujemy jego treść.
        """

        async def request() -> list[dict[str, Any]]:
            def fetch_grades_page():
                return self._client.post(
                    self._client.GRADES_URL,
                    data={"zmiany_logowanie_wszystkie": "1"},
                )

            response = await self._run_blocking(fetch_grades_page)
            rows = _behavior_table_rows(response.text)
            comment_cache: dict[str, str] = {}
            for row in rows:
                path = str(row.pop("comment_path", "") or "")
                if not path or row.get("comment"):
                    continue
                if path not in comment_cache:
                    popup = await self._run_blocking(
                        self._client.get, self._client.BASE_URL + path
                    )
                    no_access_check(BeautifulSoup(popup.text, "lxml"))
                    comment_cache[path] = _popup_comment_text(popup.text)
                row["comment"] = comment_cache[path]
            return rows

        return await self._call_with_retry("bieżącego zachowania", request)

    async def async_get_notes(self) -> list[dict[str, str]] | None:
        """Pobierz wpisy z menu ``Uczeń → Uwagi``."""

        async def request() -> list[dict[str, str]]:
            response = await self._run_blocking(
                self._client.get, self._client.BASE_URL + "/uwagi"
            )
            return _note_table_rows(response.text)

        return await self._call_with_retry("uwag", request)

    async def async_get_special_achievements(self) -> list[dict[str, str]] | None:
        """Pobierz stronę ``Uczeń → Szczególne osiągnięcia``."""

        async def request() -> list[dict[str, str]]:
            response = await self._run_blocking(
                self._client.get,
                self._client.BASE_URL + "/szczegolne_osiagniecia_ucznia",
            )
            return _achievement_table_rows(response.text)

        return await self._call_with_retry("szczególnych osiągnięć", request)

    async def async_get_messages(
        self, count: int = 10
    ) -> list[dict[str, Any]] | None:
        """Pobierz nagłówki wiadomości bez otwierania ich treści."""

        async def request() -> list[dict[str, Any]]:
            from librus_apix.messages import get_received

            messages = await self._run_blocking(get_received, self._client, 0)
            return [
                {
                    "author": message.author,
                    "title": message.title,
                    "date": message.date,
                    "href": message.href,
                    "unread": message.unread,
                    "has_attachment": message.has_attachment,
                }
                for message in (messages or [])[:count]
            ]

        return await self._call_with_retry("wiadomości", request)

    async def async_get_message_content(self, href: str) -> dict[str, str] | None:
        """Pobierz treść jednej wiadomości wskazanej przez użytkownika."""
        if not href:
            return None

        async def request() -> dict[str, str] | None:
            from librus_apix.messages import message_content

            message = await self._run_blocking(
                message_content, self._client, href
            )
            if message is None:
                return None
            return {
                "author": str(getattr(message, "author", "") or "").strip(),
                "title": str(getattr(message, "title", "") or "").strip(),
                "content": str(getattr(message, "content", "") or "").strip(),
                "date": str(getattr(message, "date", "") or "").strip(),
            }

        return await self._call_with_retry("treści wiadomości", request)

    async def async_get_homework(self) -> list[Any] | None:
        """Pobierz zadania z najbliższych 30 dni."""

        async def request() -> list[Any]:
            from librus_apix.homework import get_homework

            today = date.today()
            return await self._run_blocking(
                get_homework,
                self._client,
                today.strftime("%Y-%m-%d"),
                (today + timedelta(days=30)).strftime("%Y-%m-%d"),
            )

        return await self._call_with_retry("zadań", request)

    async def async_get_attendance(self) -> list[dict[str, Any]] | None:
        """Pobierz wpisy frekwencji ze wszystkich zwróconych semestrów."""

        async def request() -> list[dict[str, Any]]:
            from librus_apix.attendance import get_attendance

            semesters = await self._run_blocking(
                get_attendance, self._client
            )
            result: list[dict[str, Any]] = []
            for semester in semesters or []:
                for entry in semester or []:
                    result.append(
                        {
                            "symbol": str(getattr(entry, "symbol", "") or ""),
                            "typ": str(getattr(entry, "type", "") or ""),
                            "data": str(getattr(entry, "date", "") or ""),
                            "przedmiot": str(
                                getattr(entry, "subject", "") or ""
                            ),
                            "nauczyciel": str(
                                getattr(entry, "teacher", "") or ""
                            ),
                            "godzina": getattr(entry, "period", None),
                        }
                    )
            return result

        return await self._call_with_retry("frekwencji", request)

    async def async_get_announcements(self) -> list[dict[str, str]] | None:
        """Pobierz ogłoszenia szkolne."""

        async def request() -> list[dict[str, str]]:
            from librus_apix.announcements import get_announcements

            announcements = await self._run_blocking(
                get_announcements, self._client
            )
            return [
                {
                    "tytul": str(
                        getattr(announcement, "title", "") or ""
                    ),
                    "nadawca": str(
                        getattr(announcement, "author", "") or ""
                    ),
                    "opis": str(
                        getattr(announcement, "description", "") or ""
                    ),
                    "data": str(
                        getattr(announcement, "date", "") or ""
                    ),
                }
                for announcement in announcements or []
            ]

        return await self._call_with_retry("ogłoszeń", request)

    @staticmethod
    def _fetch_schedule(client: Client, today: date) -> list[dict[str, Any]]:
        """Pobierz bieżący i następny miesiąc terminarza."""
        from librus_apix.schedule import get_schedule

        months = [
            (today.year, today.month),
            (
                today.year + 1 if today.month == 12 else today.year,
                1 if today.month == 12 else today.month + 1,
            ),
        ]
        events: list[dict[str, Any]] = []
        for year, month in months:
            monthly = get_schedule(client, f"{month:02d}", str(year)) or {}
            for day_number, day_events in monthly.items():
                event_date = date(year, month, int(day_number))
                if event_date < today:
                    continue
                for event in day_events:
                    events.append(
                        {
                            "data": event_date.isoformat(),
                            "tydzien": event_date.strftime("%A"),
                            "tytul": event.title,
                            "przedmiot": event.subject,
                            "godzina": event.hour,
                            "numer_lekcji": event.number,
                            "szczegoly": event.data,
                            "href": event.href,
                        }
                    )
        return sorted(events, key=lambda item: item["data"])

    async def async_get_schedule(self) -> list[dict[str, Any]] | None:
        """Pobierz nadchodzące wpisy terminarza."""

        async def request() -> list[dict[str, Any]]:
            return await self._run_blocking(
                self._fetch_schedule, self._client, date.today()
            )

        return await self._call_with_retry("terminarza", request)

    async def async_get_schedule_content(
        self, href: str
    ) -> dict[str, str] | None:
        """Pobierz pełne szczegóły jednego wpisu terminarza."""
        if not href or "/" not in href:
            return None

        prefix, detail_url = href.split("/", 1)
        if not prefix or not detail_url:
            return None

        async def request() -> dict[str, str] | None:
            from librus_apix.schedule import schedule_detail

            details = await self._run_blocking(
                schedule_detail, self._client, prefix, detail_url
            )
            if details is None:
                return None
            return {
                str(key).strip(): str(value or "").strip()
                for key, value in details.items()
            }

        return await self._call_with_retry("treści wpisu terminarza", request)

    async def async_get_student_information(self) -> Any | None:
        """Pobierz podstawowe informacje o uczniu."""

        async def request() -> Any:
            from librus_apix.student_information import get_student_information

            return await self._run_blocking(
                get_student_information, self._client
            )

        return await self._call_with_retry("informacji o uczniu", request)

    async def async_get_timetable(
        self, monday_dates: Iterable[date]
    ) -> dict[str, Any] | None:
        """Pobierz plan dla podanych tygodni."""
        mondays = tuple(monday_dates)

        async def request() -> dict[str, Any]:
            from librus_apix.timetable import get_timetable

            weeks: list[dict[str, Any]] = []
            errors: list[dict[str, str]] = []

            for monday in mondays:
                monday_datetime = datetime.combine(monday, datetime.min.time())
                try:
                    days = await self._run_blocking(
                        get_timetable, self._client, monday_datetime
                    )
                except TokenError:
                    raise
                except Exception as err:
                    diagnostic = {
                        "week_start": monday.isoformat(),
                        "error_type": type(err).__name__,
                        "error": str(err),
                    }
                    errors.append(diagnostic)
                    _LOGGER.warning(
                        "Nie udało się pobrać planu dla tygodnia %s: %s",
                        monday.isoformat(),
                        err,
                    )
                    continue

                weeks.append({"week_start": monday.isoformat(), "days": days})

            if not weeks:
                details = "; ".join(
                    f"{item['week_start']}: {item['error_type']}: {item['error']}"
                    for item in errors
                )
                raise RuntimeError(
                    "Librus nie zwrócił planu dla sprawdzanych tygodni"
                    f" ({details or 'brak szczegółów'})"
                )

            return {"weeks": weeks, "week_errors": errors}

        return await self._call_with_retry(
            "planu lekcji", request, raise_on_failure=True
        )

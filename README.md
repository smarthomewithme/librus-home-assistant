# Librus Synergia — Smart Home With Me

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-custom%20integration-41BDF5)](https://www.home-assistant.io/)
![Version](https://img.shields.io/badge/version-1.8.0--beta.2-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Unofficial **Librus Synergia integration for Home Assistant**, developed and maintained by **Smart Home With Me**.

It brings school data from Librus into Home Assistant as normal sensors, binary sensors, calendars, To-do entities, buttons and automation events — ready for dashboards, notifications and Node-RED flows.

> This project is not affiliated with, endorsed by or supported by Librus. It uses the unofficial `librus-apix` library, so changes on the Librus side may temporarily affect data retrieval.

## Install in Home Assistant

### Choosing the API backend (experimental in 1.8.0-beta.2)

The integration offers two independent API engines:

- **Classic API (librus-apix)** remains the default. Existing configuration entries keep using it, with the same stable domain, entity IDs, timetable cache and acknowledgement state.
- **Current API (librus-synergia)** can be selected while **adding a new account**. It uses an independent session per student and supports the modern Librus login. The upstream library automatically handles kindergarten accounts where the normal timetable returns HTTP 403.

The new backend is experimental and **not yet feature-complete**. In particular, the dedicated legacy **Student → Special achievements** and **Current behaviour** HTML views are not mapped; they show no items on new-backend accounts. Certain optional school modules can also be unavailable depending on account permissions. Never switch an existing production account before checking parity. Existing accounts can deliberately switch backends in **Settings → Devices & services → Librus → Configure (Options)**. The integration validates login with the target API before saving the switch, keeps the configuration entry and entity IDs, and does not modify the old read-state store. Before switching a production student account, back up Home Assistant and expect possible differences in which school data the two APIs expose. Reconfigure only edits the password.

New API cookies are persisted in Home Assistant's private per-account storage so the same device identity can survive restarts. They are never put into Home Assistant sensor attributes or diagnostics. Treat the Home Assistant backup as sensitive because valid session cookies can authenticate to Librus.

This branch has local contract tests for normalization; it still requires testing with a real kindergarten/restricted account before being marked stable. Sending Librus messages is **not part of this API fusion beta**.

### Recommended: HACS

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=smarthomewithme&repository=librus-home-assistant&category=integration)

If the button does not open automatically:

1. Open **HACS → Integrations**.
2. Open the menu in the top-right corner and choose **Custom repositories**.
3. Add `https://github.com/smarthomewithme/librus-home-assistant`.
4. Select category **Integration**.
5. Install **Librus Synergia — Smart Home With Me**.
6. Restart Home Assistant.
7. Open **Settings → Devices & services → Add integration** and search for **Librus Synergia**.

### Manual installation

Copy `custom_components/librus_apix` into `/config/custom_components/librus_apix`, then restart Home Assistant and add the integration from **Settings → Devices & services**.

---

## What the integration can do

Version **1.8.0-beta.2** continues moving dashboard logic into native Home Assistant entities while keeping the existing 1.7.x entity IDs and read-state model compatible.

### Persistent read state — added in v1.7.0

The integration can now remember which dashboard items have already been acknowledged **without any `input_text` helper**.

Supported categories:

- grades,
- messages,
- school calendar entries,
- homework,
- current behaviour entries,
- notes.

Read state is stored separately for every configured student account and survives Home Assistant restarts. Existing grades, calendar entries, homework, behaviour entries and notes are used as the initial read baseline after upgrading, so old data does not suddenly appear as new.

Use the action:

```yaml
action: librus_apix.potwierdz_odczytanie
data:
  entity_id: sensor.librus_student_oceny
  kategoria: oceny
```

For a single list item, pass `indeks`. Messages and school-calendar entries can also use the item currently selected by their existing detail actions.

Each supported item exposes a stable local `id` plus `odczytana` / `nieodczytana` where applicable. The older `jest_nowa` / `jest_nowy` flags remain available for compatibility, but for acknowledged categories they now follow persistent unread state rather than the old “today or yesterday” window.

### Grades

- current-semester grades,
- full grade list in attributes,
- grade categories,
- teacher comments,
- overall arithmetic average,
- weighted average from Librus grade weights,
- per-subject arithmetic and weighted averages,
- dynamic subject sensors,
- new subjects can appear without restarting Home Assistant.

Version 1.7.0 also preserves the Librus grade-detail link. Non-standard grade markers such as `T` can therefore be resolved through the grade detail page instead of being shown as an unexplained letter. For point-based diagnostic tests the integration keeps the raw marker and exposes the point breakdown, with a compact display such as `T · 15/21 pkt` when Librus provides `Suma punktów`.

Behaviour values are kept separate from normal grades and are not included in grade averages.

Non-numeric descriptive assessments returned by Librus are preserved in a separate **Descriptive grades** sensor, so schools using descriptive grading do not lose those entries and they do not distort numeric averages.

### Student information

- student name,
- class,
- class register number,
- homeroom teacher,
- school,
- lucky number.

The integration intentionally does **not** copy account credentials or sections such as *My Account* / *Student Account* into entities or cache.

### Behaviour

The integration separates two different Librus concepts:

- **Classification behaviour** — semester/year evaluation,
- **Current behaviour entries** — live entries visible under the Behaviour subject.

Current behaviour entries preserve the original school code/value, category, date, teacher, semester and the actual teacher comment. The technical Librus `K` marker is treated only as an indicator that a comment exists; the integration fetches the real comment text separately.

### Notes

Dedicated **Notes** sensor from **Student → Notes** with note text, date, author, note type and category. Also included are a **New Notes** binary sensor and the `librus_apix_nowa_uwaga` event.

### Special achievements

Dedicated **Special achievements** sensor from the Librus student section. Explicit empty-state pages return `0`; common table layouts are supported and an unknown future layout keeps the previous cached value instead of reporting a false zero. New entries can trigger `librus_apix_nowe_szczegolne_osiagniecie`.

### Messages

- unread message count,
- five most recent message headers,
- full message body fetched only when explicitly requested,
- automatic refresh does not open every message,
- attachments are not downloaded automatically.

Example action:

```yaml
action: librus_apix.pobierz_tresc_wiadomosci
target:
  entity_id: sensor.librus_student_wiadomosci
data:
  indeks: 0
```

Opening the full content may mark the message as read in Librus. Version 1.7.0 also allows an explicit local acknowledgement to remain remembered if Librus updates its unread flag with a delay.

### Homework

- upcoming homework for the next 30 days,
- nearest homework sensor,
- today/overdue binary sensor,
- native read-only **Home Assistant To-do list**.

The To-do entity is intentionally read-only because the underlying Librus workflow does not provide a safe supported way to mark homework as completed.

### School calendar

- events from the current and following month,
- full list available in sensor attributes,
- details for a selected event can be fetched on demand,
- nearest test/exam sensor,
- native read-only Home Assistant calendar.

Example action:

```yaml
action: librus_apix.pobierz_tresc_terminarza
target:
  entity_id: sensor.librus_student_terminarz
data:
  indeks: 0
```

### Attendance

- full attendance entries,
- absence count,
- excused and unexcused absence counts,
- dedicated **Unexcused absences** sensor,
- releases and lateness,
- per-subject absence breakdown,
- automation-ready attributes.

The integration does not invent a percentage when the legacy attendance feed does not provide a reliable denominator of all lessons.

After the first successful attendance read establishes a baseline, a newly detected unexcused absence also emits `librus_apix_nowa_nieusprawiedliwiona_nieobecnosc` for notifications and Node-RED automations.

### Announcements

- school announcements,
- title,
- author,
- date,
- full description.

### Timetable

- current week and next week,
- classroom and teacher,
- substitutions and cancelled lessons,
- active lessons today,
- local read-only Home Assistant calendar,
- `pierwsza_lekcja_dzis_start` for alarm automations,
- dedicated **Current lesson** sensor recalculated locally every minute,
- dedicated **Next lesson** timestamp sensor recalculated locally every minute,
- deterministic matching of school-calendar entries to timetable lessons,
- ambiguous events remain available as `niedopasowane_wydarzenia` instead of being attached to a guessed lesson.

### Adaptive refresh

New installations use **Automatic mode** by default:

- weekdays 06:00–18:00 → every **5 minutes**,
- weekdays 18:00–22:00 → every **15 minutes**,
- weekends 06:00–22:00 → every **30 minutes**,
- night → every **60 minutes**.

The interval is recalculated before each cycle so a long night interval does not delay the first morning update. Each account can instead use a custom **5–360 minute** interval.

Timetable refresh remains independent: configurable **30–720 minutes**, with additional checks at **04:00** and **20:30** in the Home Assistant time zone.

This is still `cloud_polling`, not true push real-time.

### Resilience and persistent cache

The timetable has a persistent cache stored separately for each student account.

- full Librus failure → the last valid timetable remains available,
- one-week failure → cache for that week is preserved while the other week can still update,
- valid empty week → treated as current information,
- Home Assistant restart without Librus access → timetable starts from cache,
- failure of one main-data section → other sections still update and the previous valid value is retained for the failed section.

Useful timetable diagnostics include `status_danych`, `dane_aktualne`, `ostatnia_poprawna_aktualizacja`, `ostatni_blad`, `bledy_tygodni`, `pierwsza_lekcja_dzis_start` and `aktywne_lekcje_wg_daty`.

### Diagnostics

The integration includes a dedicated **Status** sensor with states `ok`, `ostrzezenie` and `blad`. It also exposes the last successful main-data update, refresh mode, current refresh interval, timetable source/cache information and partial failure details.

Version 1.8 also adds Home Assistant's native **Download diagnostics** output. It contains technical health/cache information only and deliberately excludes grades, messages, notes and other student content.

### Automation-ready entities

Depending on available Librus data, the integration provides binary sensors for new messages, new grades, new behaviour entries, new notes, upcoming calendar entries and homework due today/overdue. It also emits Home Assistant events for newly detected grades, behaviour, messages, homework, calendar entries, notes, special achievements and newly detected unexcused absences.

### Manual controls

Two buttons are available: **Refresh all data** and **Refresh timetable**.

The integration can also be **reconfigured** from Home Assistant to update the Librus password without deleting the integration entry or losing entity IDs.

If Librus explicitly rejects the stored credentials, Home Assistant starts its normal **reauthentication** flow automatically. Temporary connection failures or Librus maintenance are kept separate and do not trigger a misleading password prompt.

### Multiple students

Each Librus account is configured independently and gets its own entities, timetable cache, refresh settings, persistent acknowledgement store and diagnostics.

---

## Main entities created per student

Exact `entity_id` values depend on the student name and the existing Home Assistant entity registry.

| Type | Entity | Purpose |
|---|---|---|
| sensor | Student information | Student and class details |
| sensor | Lucky number | Daily lucky number |
| sensor | Grades | Grade count and full grade list |
| sensor | Descriptive grades | Non-numeric descriptive assessments |
| sensor | Classification behaviour | Semester/year behaviour grade |
| sensor | Current behaviour | Live behaviour entries |
| sensor | Notes | Notes from Student → Notes |
| sensor | Special achievements | Special achievements count/details |
| sensor | Grade average | Current-semester average |
| sensor | Messages | Unread messages |
| sensor | Homework | Upcoming homework |
| sensor | Calendar | Upcoming school events |
| sensor | Attendance | Absences and full attendance data |
| sensor | Unexcused absences | Count and recent entries requiring attention |
| sensor | Announcements | School announcements |
| sensor | Timetable | Active lessons today plus matched calendar events |
| sensor | Current lesson | Subject currently in progress |
| sensor | Next lesson | Timestamp of next lesson |
| sensor | Status | Integration health and diagnostics |
| sensor | Last successful update | Last successful main-data refresh |
| sensor | Nearest test | Next test/exam |
| sensor | Nearest homework | Next homework deadline |
| binary_sensor | New messages | Unread message indicator |
| binary_sensor | New grades | Unacknowledged grade indicator |
| binary_sensor | New behaviour entry | Unacknowledged behaviour indicator |
| binary_sensor | New notes | Unacknowledged note indicator |
| binary_sensor | Calendar has entries | Upcoming calendar indicator |
| binary_sensor | Homework today | Today/overdue homework indicator |
| calendar | Timetable | Read-only lesson calendar |
| calendar | School calendar | Read-only tests/events calendar |
| todo | Homework | Read-only Home Assistant To-do list |
| button | Refresh all data | Manual full refresh |
| button | Refresh timetable | Manual timetable refresh |

---

## Reliability notes

The integration is designed around real-world temporary Librus failures rather than assuming every request succeeds. It includes partial-section failure handling, timetable cache, per-week timetable recovery, login-attempt throttling during outages, status diagnostics, event baselines after restart and persistent read acknowledgements.

Because Librus is an external cloud service and this project relies on unofficial access methods, no unofficial integration can guarantee uninterrupted compatibility after changes made by Librus.

---

## Privacy

The integration runs inside your Home Assistant instance.

- credentials are stored in the Home Assistant config entry,
- Smart Home With Me does not receive your Librus username, password or school data,
- account credentials visible on Librus information pages are not copied into Home Assistant entities or the timetable cache,
- persistent read state stores only short hashed identifiers, not message/grade contents.

---

## Version

Development preview on this branch: **1.8.0-beta.2**  
Current stable release on `main`: **1.7.0**

See [CHANGELOG.md](CHANGELOG.md) for the complete development history.

---

## Credits and open-source components

This Smart Home With Me release uses:

- [`librus-apix`](https://github.com/RustySnek/librus-apix) as the classic access library,
- [`librus-synergia`](https://github.com/MichalZaniewicz/librus-synergia) (Michał Zaniewicz, MIT) as the separate modern-API client,
- [`ha-librus-synergia`](https://github.com/MichalZaniewicz/ha-librus-synergia) for the kindergarten-account compatibility reference,
- [`procaktomasz/LibrusSynergiaHA`](https://github.com/procaktomasz/LibrusSynergiaHA) for timetable and additional-activity interoperability research,
- earlier open-source work from [`LibrusSynergiaHA`](https://github.com/LukMaverick/LibrusSynergiaHA).

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [LICENSE](LICENSE) for details.

---

## Project

**Smart Home With Me**  
https://www.smarthomewithme.com

Issues and bug reports can be submitted through this GitHub repository.

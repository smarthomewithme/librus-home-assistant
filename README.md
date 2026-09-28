# Librus Synergia — Smart Home With Me

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-custom%20integration-41BDF5)](https://www.home-assistant.io/)
![Version](https://img.shields.io/badge/version-1.6.2-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Unofficial **Librus Synergia integration for Home Assistant**, developed and maintained by **Smart Home With Me**.

It brings school data from Librus into Home Assistant as normal sensors, binary sensors, calendars, To-do entities, buttons and automation events — ready for dashboards, notifications and Node-RED flows.

> This project is not affiliated with, endorsed by or supported by Librus. It uses the unofficial `librus-apix` library, so changes on the Librus side may temporarily affect data retrieval.

## Install in Home Assistant

### Recommended: HACS

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=smarthomewithme&repository=librus-home-assistant&category=integration)

If the button does not open automatically:

1. Open **HACS → Integrations**.
2. Open the menu in the top-right corner and choose **Custom repositories**.
3. Add:
   `https://github.com/smarthomewithme/librus-home-assistant`
4. Select category **Integration**.
5. Install **Librus Synergia — Smart Home With Me**.
6. Restart Home Assistant.
7. Open **Settings → Devices & services → Add integration** and search for **Librus Synergia**.

### Manual installation

Copy the folder:

```text
custom_components/librus_apix
```

into:

```text
/config/custom_components/librus_apix
```

Then restart Home Assistant and add the integration from **Settings → Devices & services**.

---

## What the integration can do

Version **1.6.2** turns Librus into a complete school-data source for Home Assistant rather than a simple grade sensor.

### Student information

- student name,
- class,
- class register number,
- homeroom teacher,
- school,
- lucky number.

The integration intentionally does **not** copy account credentials or sections such as *My Account* / *Student Account* into entities or cache.

### Grades

- current-semester grades,
- full grade list in attributes,
- grade categories,
- teacher comments,
- overall average,
- per-subject averages,
- dynamic subject sensors,
- new subjects can appear without restarting Home Assistant.

Behaviour values are kept separate from normal grades and are not included in grade averages.

### Behaviour

The integration separates two different Librus concepts:

- **Classification behaviour** — semester/year evaluation such as `excellent` / `very good` depending on the school wording,
- **Current behaviour entries** — live entries visible under the Behaviour subject.

Current behaviour entries preserve:

- original school code/value,
- category,
- date,
- teacher,
- semester,
- actual teacher comment.

The technical Librus `K` marker is treated only as an indicator that a comment exists. The integration fetches the real comment text separately.

### Notes — added in v1.6.1

Dedicated **Notes** sensor from **Student → Notes** with:

- note text,
- date,
- author,
- note type,
- category.

Also included:

- **New Notes** binary sensor,
- `librus_apix_nowa_uwaga` Home Assistant event,
- baseline protection after restart so old notes do not generate a flood of false notifications.

### Special achievements — added in v1.6.1

Dedicated **Special achievements** sensor from the Librus student section.

- explicit *no achievements* page correctly returns `0`,
- common column-table layouts are supported,
- label/value layouts are supported,
- an unknown future HTML layout keeps the previous cached value instead of reporting a false zero,
- new entries can trigger `librus_apix_nowe_szczegolne_osiagniecie`.

> Real empty-state behaviour has been verified. A real non-empty achievement entry has not yet been available on the test account, so that parser remains intentionally defensive.

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

Opening the full content may mark the message as read in Librus.

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
- lateness count,
- automation-ready attributes.

### Announcements

- school announcements,
- title,
- author,
- date,
- full description.

### Timetable

- current week,
- next week,
- classroom,
- teacher,
- substitutions,
- cancelled lessons,
- active lessons today,
- local read-only Home Assistant calendar,
- `pierwsza_lekcja_dzis_start` attribute for alarm automations,
- dedicated **Next lesson** timestamp sensor.

### Adaptive refresh — added in v1.6.0

New installations use **Automatic mode** by default:

- weekdays 06:00–18:00 → every **5 minutes**,
- weekdays 18:00–22:00 → every **15 minutes**,
- weekends 06:00–22:00 → every **30 minutes**,
- night → every **60 minutes**.

The interval is recalculated before each cycle so a long night interval does not delay the first morning update.

You can switch each student account to a custom **5–360 minute** interval.

Timetable refresh remains independent:

- configurable **30–720 minutes**,
- additional checks at **04:00** and **20:30** in the Home Assistant time zone.

This is still `cloud_polling`, not true push real-time.

### Resilience and persistent cache

The timetable has a persistent cache stored separately for each student account.

- full Librus failure → the last valid timetable remains available,
- one-week failure → cache for that week is preserved while the other week can still update,
- valid empty week → treated as current information,
- Home Assistant restart without Librus access → timetable starts from cache,
- failure of one main-data section → other sections still update and the previous valid value is retained for the failed section.

Useful timetable diagnostics include:

- `status_danych` — `librus`, `cache` or `none`,
- `dane_aktualne`,
- `ostatnia_poprawna_aktualizacja`,
- `ostatni_blad`,
- `bledy_tygodni`,
- `pierwsza_lekcja_dzis_start`,
- `aktywne_lekcje_wg_daty`.

### Diagnostics

The integration includes a dedicated **Status** sensor with states:

- `ok`,
- `ostrzezenie`,
- `blad`.

It also exposes:

- last successful main-data update,
- refresh mode,
- current refresh interval,
- timetable source/cache information,
- partial failure details.

### Automation-ready entities

Depending on available Librus data, the integration provides binary sensors for:

- new messages,
- new grades,
- new behaviour entry,
- new notes,
- upcoming calendar entries,
- homework due today or overdue.

It also emits Home Assistant events for newly detected data, including grades, behaviour, messages, homework, calendar entries, notes and special achievements.

### Manual controls

Two buttons are available:

- **Refresh all data**,
- **Refresh timetable**.

### Multiple students

Each Librus account is configured independently and gets its own:

- entities,
- timetable cache,
- refresh settings,
- diagnostics.

---

## Main entities created per student

Exact `entity_id` values depend on the student name and the existing Home Assistant entity registry.

Typical entities include:

| Type | Entity | Purpose |
|---|---|---|
| sensor | Student information | Student and class details |
| sensor | Lucky number | Daily lucky number |
| sensor | Grades | Grade count and full grade list |
| sensor | Classification behaviour | Semester/year behaviour grade |
| sensor | Current behaviour | Live behaviour entries |
| sensor | Notes | Notes from Student → Notes |
| sensor | Special achievements | Special achievements count/details |
| sensor | Grade average | Current-semester average |
| sensor | Messages | Unread messages |
| sensor | Homework | Upcoming homework |
| sensor | Calendar | Upcoming school events |
| sensor | Attendance | Absences and full attendance data |
| sensor | Announcements | School announcements |
| sensor | Timetable | Active lessons today |
| sensor | Next lesson | Timestamp of next lesson |
| sensor | Status | Integration health and diagnostics |
| sensor | Last successful update | Last successful main-data refresh |
| sensor | Nearest test | Next test/exam |
| sensor | Nearest homework | Next homework deadline |
| binary_sensor | New messages | Unread message indicator |
| binary_sensor | New grades | Recent grade indicator |
| binary_sensor | New behaviour entry | Recent behaviour indicator |
| binary_sensor | New notes | Recent note indicator |
| binary_sensor | Calendar has entries | Upcoming calendar indicator |
| binary_sensor | Homework today | Today/overdue homework indicator |
| calendar | Timetable | Read-only lesson calendar |
| calendar | School calendar | Read-only tests/events calendar |
| todo | Homework | Read-only Home Assistant To-do list |
| button | Refresh all data | Manual full refresh |
| button | Refresh timetable | Manual timetable refresh |

---

## Reliability notes

This integration is designed around real-world temporary Librus failures rather than assuming every request succeeds.

The current implementation includes:

- partial-section failure handling,
- timetable cache,
- per-week timetable recovery,
- login-attempt throttling during outages,
- status diagnostics,
- baseline detection for new items after restart.

Because Librus is an external cloud service and this project relies on unofficial access methods, no unofficial integration can guarantee uninterrupted compatibility after changes made by Librus.

---

## Privacy

The integration runs inside your Home Assistant instance.

- credentials are stored in the Home Assistant config entry,
- Smart Home With Me does not receive your Librus username, password or school data,
- account credentials visible on Librus information pages are not copied into Home Assistant entities or the timetable cache.

---

## Version

Current stable version: **1.6.2**

See [CHANGELOG.md](CHANGELOG.md) for the complete development history.

---

## Credits and open-source components

This Smart Home With Me release uses:

- [`librus-apix`](https://github.com/RustySnek/librus-apix) as the Librus access library,
- earlier open-source work from [`LibrusSynergiaHA`](https://github.com/LukMaverick/LibrusSynergiaHA).

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [LICENSE](LICENSE) for details.

---

## Project

**Smart Home With Me**  
https://www.smarthomewithme.com

Issues and bug reports can be submitted through this GitHub repository.
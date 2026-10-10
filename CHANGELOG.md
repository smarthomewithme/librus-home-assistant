# Changelog

## 1.8.0-beta.2 — Experimental API fusion

- Preserves `librus_apix` and the classic API as the default for every existing account.
- Adds an opt-in modern `librus-synergia==0.3.21` client for new accounts, with isolated per-account authentication and independently persisted private device cookies.
- Supports the current API login and delegates kindergarten timetable discovery and fallback (ordinary `Timetables` returning 403) to the typed library.
- Normalizes current-API student details, grades, grade weights, descriptive grades, behaviour grades, notes, attendance, agenda, homework assignments, announcements, received message headers and individual message details for the existing HA entities.
- Normalizes current-API lessons into the existing timetable cache model; supports blocks without lesson numbers and preserves teacher, room, cancellation and substitution status.
- Adds contract tests for the optional modern API, especially kindergarten and overlapping timetable blocks.
- Credits the related Tomasz and Michał projects, keeps the original source implementation independently authored.
- Does not activate the new API automatically on existing accounts; no message sending or AI summary in this update.
- Compatibility caveat: some HTML-only views such as Student → Special achievements and Current behaviour remain unmapped on the modern backend until live account testing.

## 1.8.0-beta.1

- Added native **Current lesson** sensor with subject, period, teacher, classroom, substitution flag and minutes remaining. The sensor recalculates locally every minute without additional Librus requests.
- Existing **Next lesson** sensor now also recalculates locally every minute, so lesson boundaries do not wait for the next timetable download.
- Added deterministic school-calendar → timetable matching. Events are attached only when the date and lesson number match, or when a missing lesson number still leaves one unambiguous subject match; ambiguous items remain explicitly unmatched instead of being guessed.
- Added Librus grade **weight** and **counts toward average** metadata using native librus-apix fields with a tooltip fallback.
- Added weighted-average attributes globally and per subject while keeping the existing arithmetic-average sensor state for backwards compatibility.
- Grades explicitly marked by Librus as not counting toward the average are excluded from both arithmetic and weighted calculations.
- Expanded attendance data with excused/unexcused counts, releases, lateness and per-subject absence breakdowns.
- Added a dedicated **Unexcused absences** sensor with recent entries.
- Added `librus_apix_nowa_nieusprawiedliwiona_nieobecnosc`, emitted only for genuinely new unexcused attendance entries after the initial baseline.
- Added a dedicated **Descriptive grades** sensor for non-numeric descriptive assessments returned by Librus; these entries stay separate from numeric averages and normal-grade events.
- Added privacy-safe Home Assistant **Download diagnostics** support. Diagnostics intentionally exclude grades, messages, notes and other student content.
- Added **Reconfigure** flow for changing the Librus password without deleting the config entry, entity IDs or dashboard references.
- Added automatic Home Assistant **Reauthentication** when Librus explicitly rejects stored credentials; network errors and Librus maintenance do not trigger a false password prompt.
- Added CI validation: Python compilation, pure timetable unit tests, Home Assistant hassfest and HACS validation.
- Added pure unit tests for current-lesson detection, calendar-to-lesson matching, weighted grade math and attendance classification.
- No Weekly AI Summary was added in this release.

## 1.7.0

- Added persistent read acknowledgements stored internally by the integration for grades, calendar entries, homework, current behaviour and notes; no `input_text` helper is required.
- Added `librus_apix.potwierdz_odczytanie` for acknowledging a whole category or one selected item.
- Read state now survives Home Assistant restarts and does not depend on a date-based “today or yesterday” flag.
- Existing data is treated as the initial read baseline on first start after upgrading, so old grades/events do not suddenly appear as new.
- Messages keep the real Librus unread flag as their primary state, while an explicit local acknowledgement can suppress delayed Librus unread updates.
- Grade entries now preserve the stable Librus `href`, which is used for persistent item identity when available.
- Non-standard grade markers such as `T` are resolved through the grade-details page. Point-based diagnostic results expose the full details and a compact display such as `T · 15/21 pkt` instead of an unexplained marker alone.

## 1.6.2

- Fixed Home Assistant Recorder warnings when timetable sensor attributes exceed the 16 KiB history-storage limit.
- Full timetable structures remain available live to dashboards, automations and Node-RED, but heavy timetable attributes are no longer duplicated into every Recorder history row.
- Lightweight timetable status, lesson counts and start timestamps continue to be recorded normally.
- No entity IDs, unique IDs or dashboard-facing attribute names were changed.

## 1.6.1

- Added a dedicated **Notes** sensor from `Student → Notes`; state is the number of entries and attributes preserve note text, date, author, note type and category as separate fields.
- Added **New Notes** binary sensor and `librus_apix_nowa_uwaga` event. The first successful read establishes a baseline, so a restart does not generate a flood of old notifications.
- Added **Special achievements** sensor from `/szczegolne_osiagniecia_ucznia`; an explicit “no achievements” page correctly returns `0`.
- Achievement parser supports common column-table layouts and label→value layouts; on an unknown future layout the integration keeps the previous cache instead of reporting a false zero.
- Added `librus_apix_nowe_szczegolne_osiagniecie` event for achievements detected after the initial baseline.
- Existing **Student information** sensor now also exposes `numer_w_dzienniku` alongside the earlier `numer_w_klasie` name.
- Login data and Librus `My account` / `Student account` sections are not copied to Home Assistant entities or cache.
- New sections use the same partial-failure resilience as the rest of the integration: a single Notes or Achievements error does not wipe previously valid data.

## 1.6.0

- Added default **Automatic** refresh mode for current school data: 5 min on weekdays 06:00–18:00, 15 min 18:00–22:00, 30 min on weekends 06:00–22:00 and 60 min at night.
- AUTO interval is recalculated before every cycle and shortened near a time-band boundary so a long night interval cannot delay the first morning refresh.
- Added **Custom interval** mode from 5–360 minutes for each student account.
- Kept the timetable on a separate 30–720 minute interval plus extra refreshes at 04:00 and 20:30.
- Existing users with a previously saved interval keep it as manual mode; new installations start in AUTO.
- **Status** sensor now exposes refresh mode and current interval.
- Integration remains `cloud_polling`; AUTO is adaptive polling, not true push real-time.

## 1.5.5

- Simplified **Current behaviour** sensor: state is the number of entries and details live in the `wpisy` attribute.
- Removed duplicated top-level behaviour attributes.
- Each behaviour entry keeps only useful fields: value, date, category, comment, teacher, semester and recent-entry marker.
- Comment popup parser removes technical Librus UI text and keeps the actual teacher comment.
- Existing behaviour `unique_id` is preserved to avoid duplicate entities after upgrade.

## 1.5.4

- Corrected the meaning of Librus `K`: it is only a comment-present marker, not the comment itself.
- Current behaviour is read directly from the dedicated Behaviour table on the grades page.
- School behaviour codes are preserved exactly as entered by the school.
- When `K` is present, the integration opens the same comment endpoint as Librus and stores the actual comment text.
- Normal grade comments were also cleaned so the integration exposes only the real comment rather than the full tooltip metadata.

## 1.5.3

- Separated **Classification behaviour** from live Behaviour-subject entries.
- Added **Current behaviour** sensor with raw school code, category, teacher comment, date, teacher and semester.
- Behaviour codes are excluded from normal grades and averages.
- Added **New behaviour entry** binary sensor and `librus_apix_nowy_wpis_zachowania` event.

## 1.5.2

- Added separate **Behaviour** classification sensor based on descriptive grades returned by Librus.
- Behaviour entries are excluded from normal grades and grade averages.

## 1.5.1

- Grades preserve the full teacher comment returned by Librus.
- `librus_apix_nowa_ocena` event includes the grade comment.

## 1.5.0

- Added binary automation entities: **New messages**, **New grades**, **Calendar has entries** and **Homework today**.
- Added **Status** sensor combining main-data health, partial failures and timetable cache status.
- Added **Nearest test** and **Nearest homework** sensors.
- Added **Last successful update** timestamp.
- New entities use already fetched coordinator data and do not add extra Librus logins.

## 1.4.1

- Fixed opening calendar entries that do not contain additional teacher text.

## 1.4.0

- Added **Attendance** sensor with full entries, absence count and lateness count.
- Added **Announcements** sensor with school descriptions.
- Added native read-only Home Assistant **Calendar** for school events.
- Added native read-only **Homework To-do** list.
- Preserved separate timetable coordinator and persistent timetable cache.

## 1.3.2

- Added opening one of the five nearest school-calendar entries on demand.
- Added `librus_apix.pobierz_tresc_terminarza` service.
- Calendar details follow the same on-demand model as full message content.

## 1.3.1

- Fixed Polish `ł` normalization when detecting cancelled lessons.

## 1.3.0

- Added per-account refresh settings.
- Split API client, main data and timetable into independent layers.
- Added dynamic subject sensors without restarting Home Assistant.
- Strengthened partial-failure data retention and week-specific timetable cache recovery.
- Added 30-second login-attempt protection during outages.
- Added Smart Home With Me branding and project URL.

## 1.2.3

- Main data refreshed every 30 minutes.
- Timetable refreshed every 120 minutes plus 04:00 and 20:30 checks.
- Added manual full refresh.
- Added on-demand full message body retrieval.

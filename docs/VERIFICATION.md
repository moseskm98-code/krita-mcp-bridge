# Verification scope / Границы проверки

## English

Native development evidence was obtained with Krita 5.3.4 on macOS. Independent review exercised the original ten named tools through real MCP in a separate official `kritarunner`: copy, layer, polygon/grayscale mask, Color Balance/Levels, visibility, order, new KRA/PNG, and KRA reopen. Tested masks had zero changes outside; reopened exports matched decoded RGBA.

The 0.2.0 development workflow also recorded a passed native region test for `export_region`, `prepare_patch`, `import_patch`, and `clone_patch`. These are results of specific local runs, not certification of every platform, input, or filter.

Public integration tests replace private photographs with generated gradients. Run the opt-in native tests and inspect their actual reports. Node unit tests cover schemas/stdio/HTTP with test backends. Python validation/region tests use pure validation and API doubles, not native Krita.

The public macOS launcher uses its own profile. Linux activation, Windows, crash recovery during native writes, filesystem races, and visual acceptance of arbitrary images are unverified. State exposes bridge-owned copies only. Unchanged source coordinates do not validate added surroundings or hidden geometry.

## Русский

Реальные проверки выполнены на Krita 5.3.4 в macOS. Независимое ревью вызвало исходные десять инструментов через настоящий MCP: копия, слой, полигонная и серая маски, Color Balance/Levels, видимость, порядок, новые KRA/PNG и повторное открытие KRA. Вне проверенных масок изменений не было; повторные экспорты совпали по RGBA.

В разработке 0.2.0 зафиксирован успешный реальный тест четырёх операций с фрагментами. Это результаты конкретных запусков; они не подтверждают все платформы, входы и фильтры.

Публичные интеграционные тесты используют синтетический градиент. Проверяйте фактический отчёт явного native-теста. Node unit-тесты используют тестовые backend; Python-тесты проверяют валидацию и API doubles.

Публичный launcher использует отдельный профиль. Linux, Windows, аварии записи, гонки путей и визуальное качество произвольных изображений не подтверждены. State показывает только копии моста. Координаты исходного слоя не доказывают правильность нового окружения и скрытой геометрии.

## Public snapshot test / Тест публичной копии — 2026-10-07

The publication copy passed 11 Node tests, 27 Python tests, and both native synthetic-fixture tests on Krita 5.3.4/macOS. Real-test backend settings/resources were isolated, and the original settings file hashes remained unchanged. Across the tests all fourteen named tools were exercised. Full and region KRA round trips matched RGBA; the tested edits changed zero pixels outside the masks.

Публичная копия прошла 11 Node-тестов, 27 Python-тестов и оба реальных теста с синтетическими изображениями. Проверены все 14 инструментов. Полный и локальный KRA roundtrip совпали по RGBA; вне масок изменений не было. Настройки тестового backend изолированы; хеши исходных настроек сохранились.

These checks do not resolve the recovery issues listed in KNOWN_ISSUES.md.
Эти проверки не исправляют ошибки восстановления из KNOWN_ISSUES.md.

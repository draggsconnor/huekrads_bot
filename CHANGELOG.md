# Changelog

Все значимые изменения проекта будут документироваться здесь.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
и проект придерживается [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

### Added
- Структура проекта с каталогами `docs/` и `journal/`

## [0.4.0] - 2026-10-07

### Added
- Полная миграция: `bot.py` → `src/` (main, models, config, handlers, combat, storage, scheduler)
- Баланс undead: skeleton warrior (120→90 HP), zombie brute (200→150 HP), necromancer (100), ghost (60)
- Система достижений с 20+ ачивками + прогресс-бар
- Scheduler на asyncio вместо threading
- Docker multi-stage + healthcheck + non-root
- README с диаграммами, CHANGELOG, Dev Journal

### Changed
- Архитектура: монолитный bot.py → модульная структура src/

## [0.3.0] - 2026-10-07
- Босс undead: некромант, скелет-воин, зомби-брут, призрак
- Боевая система: случайные противники, механики undead
- Улучшенный UI эмодзи

## [0.2.0] - 2026-10-07
- Adventures (5 локаций)
- Квесты с диалогами
- Работа "Курьер" каждые 4 часа
- Классы персонажей (воин, лучник, маг)
- Случайные имена
- Duel (PvP) с быстрым и обычным режимами
- Dice (казино)
- Inventory и Equip
- Крафт
- Training

## [0.1.0] - 2026-10-07
- MVP: регистрация, профиль, арена

## История разработки
Подробный журнал разработки с архитектурными решениями, командными обсуждениями и итерациями — см. [Документация. Структура проекта, контакты, ❤️](docs/JOURNAL.md)
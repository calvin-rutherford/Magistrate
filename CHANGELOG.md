# Magistrate Changelog

## [Unreleased] - The Magistrate Government Pivot

### Added
- **Project Rebranding**: Omnigent has been officially renamed to **Magistrate**, completing the transition from a simple multi-agent dashboard to a full AI-native engineering government.
- **Constitutional Database Models**: Deployed a massive new ORM schema representing the Federal hierarchy, including `UserPresident`, `Constitution`, `Branch`, `Department`, `Agency`, `State`, `City`, and `CivilServantAgent`.
- **Legislative Branch**: Implemented the `LegislativeService` allowing agents and the President to propose `Laws`, issue `Regulations`, and dictate system-wide `Policies`.
- **Judicial Branch**: Implemented the `JudicialService` handling `CourtCase` creation and the issuing of `Rulings` to block or allow code execution (replacing the primitive approval queue).
- **Executive Operations**: Refactored the Broker into the `ExecutiveOffice` powered by Gemini. Added the `ExecutiveService` enabling the President to issue `ExecutiveOrders` and launch `Fleets`, `Ships`, and `Missions`.
- **Testing Infrastructure**: Added `pytest` and `pytest-django` configurations. Bootstrapped the `backend/tests/` directory with comprehensive unit tests enforcing branch boundaries.
- **Seed Data Generation**: Added the `seed_government` Django management command to immediately instantiate the foundational Executive, Legislative, and Judicial branches.

### Changed
- **Task Orchestration**: Deprecated the generic `run_agent_loop` in favor of `run_mission_loop`, where isolated `Ships` execute `Missions` guided by their assigned `Captain` and `Crew`.
- **Documentation**: Overhauled `vision.md` and `architecture.md` to establish the new Federalism and Institutional metaphors. Added `constitution.md`.

### Removed
- **Flat Agent Hierarchies**: Eradicated the primitive `Agent` model where all LLMs were peers. Replaced with `CivilServantAgent` enforcing Ranks and Certifications.

## [Legacy] - V1 Control Plane Pivot

### Added
- **Native CLI REPL**: Replaced the rigid Textual TUI with a standard, fluid terminal REPL using `prompt_toolkit`.
- **Persistent Sandboxing**: Worker execution decoupled into isolated, persistent `omnigent-sandbox` Docker containers.

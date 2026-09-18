# Project Documentation

This folder contains the main documentation for the outdoor trail intelligence project.

## Put this folder here

Copy the entire `docs` folder into the project root:

```text
projectfirstversion/
├── backend/
├── frontend/
├── data/
├── models/
├── notebooks/
├── scripts/
├── docs/                 <- put this folder here
├── .env
├── .env.example
└── .gitignore
```

Do not put these documents inside `backend/` or `frontend/`.

## Documents

### `PROJECT_OVERVIEW.md`
What the project is, the problem it solves, the overall goal, and the end-to-end flow.

### `PHASE_PROGRESS_REPORT.md`
The project history from the initial planning and database/backend work through the current Phase 4 work.

### `TECH_STACK_AND_FILE_MAP.md`
What each important technology and main code file does, in simple language for explaining the project to an instructor.

### `DATA_SOURCES_AND_PIPELINE.md`
Where data comes from and how it moves through the system from search and OSM through elevation/weather and the planned ML/recommendation layers.

### `CURRENT_STATUS_AND_NEXT_STEPS.md`
The honest current state, known problems, and the order of the next implementation steps.

### `INSTRUCTOR_PRESENTATION.md`
A simple speaking note for presenting the project from the beginning to the current stage.

## Important status note

The current working OSM baseline for development is the 6,591-line `osm.py` version. It is a working baseline with known trail-discovery issues; documentation should not describe trail discovery as final until it is validated across multiple locations.

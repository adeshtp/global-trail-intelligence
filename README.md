# Global Trail Intelligence

A data-driven outdoor trail intelligence platform that combines geospatial data, terrain analysis, weather information, machine learning, and 3D visualization.

## Project Overview

The project allows a user to search for a mountain, trail, or location and explore available trail information through an interactive 3D map.

For a selected trail, the system is designed to provide:

* Trail distance
* Elevation and elevation gain/loss
* Slope information
* Weather information
* Trail condition likelihood
* Difficulty estimation
* General suitability information
* Gear recommendations
* Product information and links

A RAG-based assistant is planned as a later stage to provide grounded explanations about trails and recommendations.

## Technology Stack

### Backend

* Python
* FastAPI
* PostgreSQL
* PostGIS

### Frontend

* Next.js
* React
* TypeScript
* Tailwind CSS
* Cesium

### Geospatial and Data Processing

* Pandas
* Polars
* GeoPandas
* Shapely
* Rasterio
* PyProj

### Planned Data Science / AI

* Feature engineering
* Machine learning
* Trail difficulty estimation
* Trail condition likelihood
* Suitability analysis
* Gear recommendation
* RAG + LLM

## Data Sources

The project is designed around dynamic and open geographic/environmental data where available.

Primary sources include:

* OpenStreetMap — trail and geographic data
* Elevation/DEM data — terrain and elevation analysis
* Weather data/API — weather and rainfall-related information
* Official trail/park sources — additional information when available

## Current Development Status

The project is currently in **Phase 4**.

The current focus is improving the trail-discovery layer, especially how OpenStreetMap ways and relations are interpreted and combined into meaningful trail candidates.

Current work includes:

* OSM relation parsing
* Multi-way trail geometry assembly
* Trail component identification
* Trail candidate filtering
* Trail ranking and selection
* Selected trail geometry handling
* Elevation integration
* Weather integration

Trail discovery is **still under development and validation**. OSM coverage and tagging vary by location, so results are not yet considered final.

## Project Structure

```text
global-trail-intelligence/
│
├── backend/
│   └── FastAPI backend, APIs, services and data processing
│
├── frontend/
│   └── Next.js, React, Cesium and user interface
│
├── data/
│   └── Project data and datasets
│
├── models/
│   └── Machine-learning models and related code
│
├── notebooks/
│   └── Data Science experiments and analysis
│
├── scripts/
│   └── Utility and processing scripts
│
├── docs/
│   └── Project documentation and progress reports
│
├── .env.example
├── .gitignore
└── README.md
```

## Development Pipeline

```text
User Search
    ↓
Location Resolution
    ↓
OpenStreetMap Trail Discovery
    ↓
Trail Selection
    ↓
Trail Geometry
    ↓
3D Cesium Visualization
    ↓
Elevation + Weather
    ↓
Feature Engineering
    ↓
Difficulty Estimation
    ↓
Condition Likelihood
    ↓
Suitability
    ↓
Gear Recommendation
    ↓
Product Information
    ↓
RAG / LLM Explanation
```

## Documentation

Detailed project documentation is available in the [`docs/`](docs/) folder.

Important documents include:

* [Project Overview](docs/PROJECT_OVERVIEW.md)
* [Phase Progress Report](docs/PHASE_PROGRESS_REPORT.md)
* [Tech Stack and File Map](docs/TECH_STACK_AND_FILE_MAP.md)
* [Data Sources and Pipeline](docs/DATA_SOURCES_AND_PIPELINE.md)
* [Current Status and Next Steps](docs/CURRENT_STATUS_AND_NEXT_STEPS.md)
* [Instructor Presentation](docs/INSTRUCTOR_PRESENTATION.md)

## Project Status

This repository contains the current development baseline of the project. Features are being developed incrementally and validated before being treated as final.

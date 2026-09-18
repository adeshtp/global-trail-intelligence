# Technology and Main File Map

## Backend

### `backend/app/main.py`
- Main FastAPI application entry point.
- Connects/registers the backend routes.

### `backend/app/core/config.py`
- Loads project configuration and environment variables.
- Holds settings such as the database URL and external API keys/tokens.

### `backend/app/core/database.py`
- Creates the database engine/connection used by the application.

### `backend/app/core/init_db.py`
- Creates database tables from the application models.

### `backend/app/models/trail.py`
- Defines the trail-related database model/data structure.

### `backend/app/routes/search.py`
- Provides location search/geocoding.
- Converts user text into usable geographic coordinates and place information.

### `backend/app/routes/osm.py`
- Main OpenStreetMap trail-discovery and processing logic.
- Gets OSM data.
- Parses ways and relations.
- Builds/cleans trail geometry.
- Calculates distance and other trail metadata.
- Filters/ranks trail candidates.
- Returns trail discovery results and selected-trail geometry.
- This is currently the main backend file because trail quality is the present bottleneck.

### `backend/app/routes/elevation.py`
- API endpoint for elevation analysis of a selected trail geometry.

### `backend/app/routes/weather.py`
- API endpoint for weather information for a selected trail location.

### `backend/app/services/elevation.py`
- Contains the elevation-processing/provider logic.
- Used to derive elevation profile, gain/loss and slope from trail geometry and elevation data.

### `backend/app/services/weather.py`
- Handles the external weather-provider request and response processing.

### `backend/app/schemas/`
- Defines request/response data structures exchanged by the API.

### `backend/app/ml/`
- Reserved for feature engineering, training, evaluation and prediction logic.
- Difficulty and condition models are future/current analytical work, not the finished part of the application.

### `backend/app/rag/`
- Reserved for the later retrieval-augmented question-answering layer.

## Frontend

### `frontend/app/page.tsx`
- Landing/start page.
- Begins the user's search experience.

### `frontend/app/explore/page.tsx`
- Main Explore page.
- Connects location search, trail discovery, trail selection and the map.
- Stores the selected trail geometry for later analysis.

### `frontend/components/ExploreSearch.tsx`
- Search box and location-search logic.
- Calls the backend search API.
- Distinguishes broad place searches from specific outdoor-feature searches.

### `frontend/components/TrailSidebar.tsx`
- Displays discovered trail cards.
- Shows trail metadata such as name, type, distance, difficulty and surface when available.
- Sends the selected trail back to the Explore page.

### `frontend/components/CesiumMap.tsx`
- Initializes Cesium.
- Loads OpenStreetMap imagery.
- Displays the searched location.
- Draws discovered trail geometries.
- Highlights the selected trail geometry.
- Provides map controls and 3D terrain mode.

## Technology roles

### Python
- Backend and data-processing language.

### FastAPI
- REST API framework connecting the frontend to the processing layer.

### PostgreSQL
- Main relational database.

### PostGIS
- Spatial database extension for geographic data and spatial operations.

### Next.js / React / TypeScript
- Frontend application framework and UI layer.

### Tailwind CSS
- Frontend styling and layout.

### Cesium
- 3D globe and terrain visualization.

### Pandas
- Tabular data cleaning and feature preparation.

### Polars
- Faster dataframe processing when useful for larger data operations.

### GeoPandas
- Geographic dataframe and spatial-data processing.

### Shapely
- Geometry operations such as LineStrings, MultiLineStrings and spatial checks.

### Rasterio
- Raster/DEM data processing for terrain/elevation work.

### PyProj
- Coordinate systems and coordinate transformations.

### Machine-learning libraries
- Used later for feature-based prediction and evaluation.

### Postman
- API testing and backend validation during development.

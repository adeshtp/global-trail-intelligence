# Data Sources and Processing Pipeline

## 1. Location search

### Source
OpenStreetMap Nominatim / location search service.

### Purpose
Convert the user's text such as:

- `Munnar`
- `Wayanad`
- `Meesapulimala`
- `Chembra Peak`

into a place result and geographic coordinates.

### Output
- Latitude
- Longitude
- Display name
- OSM/place metadata when available

---

## 2. Trail discovery

### Main source
OpenStreetMap.

### Main OSM objects
- Nodes
- Ways
- Relations

### Important distinction
A `way` is a mapped geometry segment.
A `relation` can organize multiple ways into a logical route.

Because of this, the backend cannot simply assume that every OSM way is a complete trail.

### Processing
```text
OSM raw data
  ↓
Parse tags / geometry
  ↓
Identify trail-related candidates
  ↓
Handle relations and connected components
  ↓
Clean geometry
  ↓
Filter weak/irrelevant candidates
  ↓
Rank candidates
```

---

## 3. Selected trail geometry

The selected trail is converted into a geometry contract that can represent:

- `LineString`
- `MultiLineString`

Coordinates are handled in geographic form so the same selected geometry can be used by the map and later analysis services.

---

## 4. Elevation

### Source/role
A DEM/elevation data source is used to obtain elevation values along the selected trail geometry.

### Output
- Minimum elevation
- Maximum elevation
- Elevation profile
- Elevation gain
- Elevation loss
- Average slope
- Maximum slope

### Pipeline
```text
Selected trail geometry
  ↓
Sample points along geometry
  ↓
Get DEM/elevation values
  ↓
Build elevation profile
  ↓
Calculate gain/loss
  ↓
Calculate slope features
```

---

## 5. Weather

### Source/role
A weather API is used for weather information near the selected trail.

### Current representation
A representative point from the selected trail geometry is used for the weather query.

### Pipeline
```text
Selected trail geometry
  ↓
Representative coordinates
  ↓
Weather API
  ↓
Current/forecast weather data
```

---

## 6. Feature engineering

Once trail, elevation and environmental information is reliable, the data can be converted into model features.

Examples:

- Distance
- Elevation gain/loss
- Average slope
- Maximum slope
- Surface
- Trail type
- Visibility
- Weather variables
- Recent rainfall variables

---

## 7. Difficulty model

### Goal
Estimate how difficult a trail is.

### Conceptual pipeline
```text
Trail + terrain features
  ↓
Feature engineering
  ↓
Training / testing data
  ↓
ML model
  ↓
Difficulty prediction
  ↓
Model evaluation
```

The final model should be selected based on actual training/evaluation results rather than assumed in advance.

---

## 8. Condition likelihood

### Goal
Estimate the likelihood of trail conditions being affected by wetness/rainfall/environmental factors.

### Important wording
Use **likelihood** or **estimate**, not a definitive statement about real-time trail condition.

### Conceptual pipeline
```text
Trail features
+
Weather / rainfall
+
Environmental features
  ↓
Feature engineering
  ↓
Condition-likelihood model/rules
  ↓
Condition likelihood
```

---

## 9. Suitability

### Current scope
General suitability based on the trail and its conditions.

### Future scope
Personalized suitability using optional user information.

```text
Current:
Trail + difficulty + conditions
        ↓
General suitability

Future:
Trail + difficulty + conditions + user profile
        ↓
Personalized suitability
```

---

## 10. Gear and products

```text
Trail intelligence
  ↓
Required gear categories
  ↓
Product discovery
  ↓
Product information
  ↓
Images + external links
```

The recommendation layer should be based on analytical results rather than independent arbitrary product generation.

---

## 11. RAG / LLM

The later RAG layer will retrieve trail, environmental, analytical and gear evidence before generating an explanation.

```text
Project/trail evidence
  ↓
Retrieval
  ↓
Context
  ↓
LLM
  ↓
Grounded explanation
```

# Instructor Presentation — Simple Speaking Note

## 1. What is the project?

> We are building a data-driven outdoor trail intelligence platform. A user searches for a mountain, trail or location, the system discovers relevant trails, shows them on a 3D map, and then analyses the selected trail.

## 2. What technologies are we using?

> The backend uses Python and FastAPI. We use PostgreSQL with PostGIS for spatial data. The frontend uses Next.js, React, TypeScript and Tailwind CSS. Cesium is used for the 3D map and terrain.

## 3. Where do the trail data come from?

> Trail discovery mainly comes from OpenStreetMap. We use OSM ways, nodes, relations, tags and geometry instead of manually maintaining a fixed trail list.

## 4. Why is OSM processing difficult?

> One real trail can be represented by multiple OSM ways or a relation containing multiple ways. Broad searches can also return small path fragments and unrelated nearby paths. So we need to identify meaningful trails before doing further analysis.

## 5. What did we build first?

> First we built the database and backend foundation, then the frontend and Cesium map, and now Phase 4 is focused on improving trail discovery and selected-trail geometry.

## 6. What does `osm.py` do?

> It is the main trail-discovery file. It gets OpenStreetMap data, processes ways and relations, builds trail geometry, calculates trail information, filters candidates and returns the trails to the frontend.

## 7. What does the frontend do?

> The search component finds the location, the Explore page connects the API results, the trail sidebar shows the discovered trails, and Cesium displays the searched location and selected trail in 3D.

## 8. What are we doing with elevation?

> Once a trail is selected, we use its actual geometry to derive elevation profile, elevation gain and loss, average slope and maximum slope.

## 9. What are we doing with weather?

> We use representative coordinates from the selected trail geometry to query the weather service.

## 10. What is difficulty estimation?

> Difficulty asks how hard the trail is. We plan to use features such as distance, elevation gain and slope and then train and evaluate a machine-learning model.

## 11. What is trail condition likelihood?

> Condition likelihood asks what conditions the trail may be affected by. We combine trail and environmental features with weather and rainfall information. We use likelihood language because the system cannot directly observe every trail in real time.

## 12. What is suitability?

> Suitability is different from difficulty. Difficulty describes the trail, while suitability describes whether the trail appears appropriate under the available trail and environmental conditions. Personalized suitability using user information is future scope.

## 13. What comes after that?

> The intended chain is trail intelligence, then difficulty and condition likelihood, then general suitability, then gear requirements and product discovery, and finally RAG/LLM explanations.

## 14. What is the current problem?

> The current bottleneck is trail quality. Some results are still small fragments, some connected trails are still separated, and some irrelevant paths can appear. Naming can also be weak. We are fixing the trail-data layer before treating the later intelligence outputs as final.

## 15. What are the next steps?

> First we validate trail discovery across several locations. Then we fix trail identity, geometry and filtering. After that we directly validate the selected geometry with elevation and weather. Once the data foundation is reliable, we build feature engineering, difficulty ML, condition likelihood, general suitability, gear and finally RAG.

## 16. One-line project flow

```text
Search
→ Location
→ OSM trails
→ Trail selection
→ 3D Cesium
→ Elevation + Weather
→ Features
→ Difficulty
→ Condition likelihood
→ Suitability
→ Gear
→ Products
→ RAG
```

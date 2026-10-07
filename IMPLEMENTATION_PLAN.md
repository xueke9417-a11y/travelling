# Travel planner integration — 2026-10-07

Goal: complete confirmed request → real POIs → daily routes → weather → recalculable budget in the existing Streamlit application.

Architecture: keep Agno/DeepSeek for clarification. Use a deterministic itinerary workflow with selected real POIs, Amap route/forecast APIs and Python arithmetic. Do not give the model authority over distances, forecasts or money. No new dependencies or credentials.

1. Write unittest coverage in `test_planner.py`: invalid dates, fake ready flags, money calculations, missing routes, malformed API responses, weather fallback and full fixture-based workflow. Observe failures before implementation.
2. Implement `services.py`: sanitized API failures, city-restricted POI search, driving/transit routes, forecast date matching. Network timeouts; no raw exception/URL/key in UI.
3. Implement `planner.py`: validated 1–7 day request, up to 14 user-selected POIs, deterministic geographic grouping, lodging return legs, source-labelled output, budget using Decimal. Unknown route costs remain unknown, never zero.
4. Update `main.py` clarification validation and error handling; update `app.py` with confirmation fields, POI selection, generation progress, retry, route/weather rendering, editable budget, JSON download and reset. Use per-session agent rather than shared cached agent.
5. Test offline with unittest and Streamlit AppTest; test live Amap driving/transit/weather and DeepSeek. Record exact verified scope in README. Copy reviewed files back with filesystem approval.

Boundaries: one city/region, one lodging base for a trip, driving or public transport (mixed travel requires explicit selection), no real-time booking, no route optimality guarantee. Days with no assigned stops are free days. Budget covers the selected local route and user-provided estimates only. Keep existing `.env` untouched. No Git commit/push.

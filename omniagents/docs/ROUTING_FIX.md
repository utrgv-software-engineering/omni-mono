# Studio Routing Migration Checklist

- [x] Audit current navigation surfaces (sidebar, headers, callbacks) and note which ones set `activeSection` or related state. (Dashboard home buttons, `StudioSidebar`, headers, and evaluation navigation mutate `activeSection`/selection state.)
- [x] Add `react-router-dom` dependency and wrap `App` with `BrowserRouter` so routes become the source of truth.
- [x] Define canonical routes (`/`, `/logs`, `/playground`, `/agents`, `/agents/:agentKey`, `/evaluation`) and decide query params for selections (`project`, `workflow`, `conversation`, `trace`, `mode`, `tab`).
- [x] Extract URL helper utilities to build/update route paths and synchronize search params without clobbering unrelated values.
- [x] Refactor `Dashboard` to derive visible content from router state instead of local `activeSection` tracking.
- [x] Replace stateful navigation handlers (home buttons, sidebar entries, evaluation links) with `navigate`/`Link` usage.
- [x] Sync Zustand selections with URL params on load and mirror future user interactions back into the URL while preventing feedback loops.
- [x] Restore conversation/trace/view mode state from URL when landing on `/logs`; handle missing entities gracefully by clearing invalid params.
- [x] Encode agent selection via `/agents/:agentKey` route and ensure deep links initialize `AgentsView` correctly.
- [x] Persist evaluation tab in URL (`?tab=`) and hydrate `EvaluationView`/`EvaluationHeader` from the query.
- [x] Ensure playground context (project/session) updates URL when available and clears on exit.
- [x] Update cross-section navigation hooks (e.g., `onNavigateToLogs`) to emit route transitions with appropriate params.
- [x] Remove redundant local state resets now handled via routing and confirm store resets still occur when navigating home.
- [ ] Smoke-test each route directly, include refresh/deep-link scenarios, and adjust tests/docs if needed.

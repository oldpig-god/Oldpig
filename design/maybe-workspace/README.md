# Maybe-inspired research workspace

2026-11-14. Design reference: https://github.com/maybe-finance/maybe
Reviewed the application's layout, dashboard, net-worth chart partial, and design tokens:
- app/views/layouts/application.html.erb
- app/views/pages/dashboard.html.erb
- app/views/pages/dashboard/_net_worth_chart.html.erb
- app/assets/tailwind/maybe-design-system.css

Applied the composition principles to the existing research application: an 88px navigation rail, contextual research-group sidebar, neutral grayscale surfaces, clear primary action, summary before chart/detail, contextual controls. Implemented independently in the existing HTML/CSS/JS; no Maybe logos, brand, or application source copied.

Research page: group membership in the side panel; portfolio metadata and filters above compact company cards; three initial evidence summaries, expandable full evidence and financial-analysis links. Group management, reminders and chart preferences remain available on demand. Names resolved through existing inventory search without price fetches.

Comparison page: two clear objects, period, primary action. Rolling window/weights/cost/source assumptions moved into an expandable section. Successful generation scrolls and focuses the report title. Invalid empty industry selections now explain how to recover.

Confirmed production defect before changes: `/api/comparison/run` rejected `lookback=250` with HTTP400, despite the shared UI offering it. Securities supported 0/60/126/252/756; industry supported 0/60/120/126/250/252/756. Both now use comparison.LOOKBACKS. A regression test submits each visible window through the securities API and verifies invalid values still fail. Industry coal/components returned200 before changes: no current industry-source outage was reproduced.

Validation: comparison and research suites (37 tests) passed, plus browser flows using real daily data. Both comparison modes, disclosure/filter controls, report navigation, tools menu and Escape, light/dark themes, 390/760/800/1024/1440 widths checked. Screenshots in this directory; production verification recorded after deployment.

Final verification: all214 Python unit tests passed (20.784s). Production HTTP function Active; browser verified the research page, industry result,120-day securities comparison, responsive layouts and dark mode. A separate production250-day securities comparison returned200 with250 valid samples, where the old version returned400. Final tools menu opens on demand, supports Escape, and indicates the active tools route without covering the page on arrival.

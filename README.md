# FungiSync

A standalone FungiSync distribution operations application with a cinematic mushroom-cultivation landing page and a forest-glass Overview/Replanning treatment inspired by the supplied UI reference. Overview uses a local looping video feature with operational statistics; the walkthrough is collapsed by default. Grower, Buyer, Matching, Allocation, Dispatch, Reports, Agent, and Settings data surfaces use calm light panels with a dark navigation rail. Background motion plays automatically during normal use and respects reduced-motion preferences; the dashboard includes an English/Hindi/Tamil language selector. It runs in a modern browser without a build step or package installation.

## Run

Open `index.html` in a browser. For a local web server, run `python -m http.server 8000` from this folder and visit `http://localhost:8000`.

## Landing page

The public landing page uses the local looping mushroom-forest video as its full-height hero, with a dark readability veil and a poster image fallback. It includes restrained pointer parallax, a slow camera drift, reduced-motion support, a six-step workflow, explicitly labelled local demo metrics, six module cards connected to the real workspaces, and a replanning preview. Flow lines are derived from local sample records and are not a geographic map or live route. The hero atmosphere is CSS-based, not a WebGL/Three.js model. The fallback poster is `fungisync-hero.png`; the video file is `fungisync-overview.mp4`.

## Overview video

Overview includes a local muted 720p MP4 background (`fungisync-overview.mp4`) with `preload="metadata"`; the original mushroom hero image remains its poster and failure fallback. Motion plays automatically, pauses while the document is hidden, and respects reduced-motion preferences. The clip is Edie Reed's forest fungi footage, downloaded from [Pexels](https://www.pexels.com/video/forest-mushrooms-5684469/) under its free license. A static image remains visible when video playback is unavailable.

## Sign-in

The app opens on a sign-in screen. A production authentication service was not supplied, so password sign-in reports that it is unavailable and clears the entered password. Use **Continue in demo mode** to open the sample workspace. The demo session lasts for the current browser tab and **Sign out** returns to the sign-in screen. This is not production authentication.

## Workspaces

- Overview dashboard with dataset-derived totals, charts when Chart.js is available, an illustrative grower-to-buyer flow map, and event summaries
- Grower and harvest registry with search, sort, CSV export, details, editing, and validation
- Buyer and order registry with cancellation events and order impact tracking
- Eligibility-based supply and demand matching with animated batch-to-order flow lines
- Quantity, species, grade, grower, and date checked allocation proposals with coordinator review
- Capacity-checked collection and delivery stop proposals derived from approved or revised allocations
- Exception analysis, six-stage event timeline, revision comparison, and alternative-buyer state
- Agent monitor that distinguishes local evidence from backend telemetry
- Operational reports and CSV export
- Persistent local records, JSON backup and restore, and a reset to starter data

## Data and deployment notes

Local changes are stored in the current browser profile using `localStorage`. JSON export provides a portable backup. The uploaded source did not include a FastAPI service, API schema, authentication configuration, routing/traffic service, or AI-agent telemetry. For that reason, this source does not claim server synchronization, enforceable role permissions, live geographic routing, trip execution, buyer acceptance, or model activity. Those require the corresponding production services.

The starter dataset is illustrative. Browser storage is appropriate for a single-browser demo, not authoritative multi-user business records.

## Verify

Run the built-in dependency-free workflow checks with Node.js 18 or later:

```sh
node verify-app.cjs
```

The check exercises login and demo entry, English/Hindi/Tamil options, ambient motion controls and page scope, all ten workspaces, grower creation, harvest quantity validation, matching, allocation validation and approval, dispatch capacity and stops, order cancellation, impact analysis, revised allocation and dispatch proposals, and persistence after app reinitialization. The standalone HTML project has no production build command; use the source directly or serve the `outputs` folder with Python's local HTTP server.

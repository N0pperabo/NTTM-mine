# N0PPERABO (NTTM) Design System & Brand Direction

## 1. Product Identity & Narrative
**N0PPERABO** (野箆坊 - The faceless entity) is an offline-first, stealth proxy/runner browser interface powered by GitHub Actions. It is an intentional, privacy-preserving, high-performance tool built for browsing in restricted network environments.

## 2. Visual Character
- **Tone**: Stealth, tactical, precise, disciplined.
- **Aesthetic**: Matte obsidian & deep slate surfaces, hairline structural borders, high-contrast white vector branding, crisp typography.
- **Anti-Patterns Rejected (Anti-Slop Compliance)**:
  - No decorative emojis in UI chrome or action buttons.
  - No generic blue-to-cyan/purple gradient washes or radial background glow blobs.
  - No excessive glassmorphism (`backdrop-filter: blur`) layered everywhere; surfaces are solid, tactile, and grounded.
  - No endless looping animations, pulsing dots, or floating hero elements.
  - No decorative pill badges duplicating titles.

## 3. Color Architecture
- **Base Canvas**: `#090b10` (True Deep Charcoal/Obsidian)
- **Surface Elevation 1 (Toolbar & Inputs)**: `#11141c`
- **Surface Elevation 2 (Cards, Flyouts, Modals)**: `#161b26`
- **Hairline Borders**: `#232a3b` (Subtle 1px structural separation)
- **Focus / Active Stroke**: `#3b82f6` (Cobalt Precision Accent)
- **Text Primary**: `#f8fafc` (Near White, High Contrast)
- **Text Secondary / Muted**: `#94a3b8`
- **Text Technical / Monospace**: `#64748b`
- **State Colors**:
  - Direct Live Mode: `#10b981` (Emerald)
  - Batch Queue Mode: `#f59e0b` (Amber)
  - Action / Error: `#ef4444` (Crimson)

## 4. Typography & Iconography
- **Body & Controls**: Vazirmatn variable (bundled woff2, OFL license) chosen for a Persian-first UI; fallback system sans (`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`)
- **Technical & Addresses**: Monospace (`ui-monospace, "Cascadia Code", "Fira Code", monospace`)
- **Icons**: Clean monochrome SVG line vectors, semantic and purpose-bound.

## 5. Motion Dials
- **ENERGY**: 2 (Disciplined, steady, focused)
- **RHYTHM**: 2 (Clear functional zoning between browser controls and web content)
- **MOTION**: 1 (Immediate hover/active state feedback only; 150ms cubic-bezier transitions; 0 perpetual idle loops)
- **Start page backdrop (owner-mandated, 2026-11-02)**: halftone dot texture, inverted from the linked reference; black field `#000000` with white dots of varying size on a jittered lattice, occasional blank cells (measured from the reference image). The Digital Flame was removed by owner decision the same day; do not reintroduce it.

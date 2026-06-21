# Sunrise UX Fixes

## Problem Summary

The user tested the app and found two problems with the Sunrise feature:

1. **Unclear duration** — How long does it last?
2. **UI becomes unreadable during sunrise** — The bright gold canvas (#FFD700) behind the glass panels makes the light-gray text (#e0e0e0) impossible to read.

---

## Investigation Results

### Duration
- **Current:** 60,000 ms (60 seconds) — hardcoded as `SUNRISE_DURATION = 60000` in `static/index.html` line 834.
- The audio fades out over the last 5 seconds, and the entire sequence resets after `SUNRISE_DURATION + 200` ms.
- After sunrise ends, the canvas **stays gold** — there is no transition back to the time-aware background color. This is a secondary bug but worth fixing.

### UI readability during sunrise
- The canvas (`#nebula`) sits at `z-index: 0` (behind all UI).
- Glass panels use `background: rgba(255, 255, 255, 0.05)` (5% white) + `backdrop-filter: blur(12px)`.
- During sunrise the canvas turns bright gold (#FFD700 / `{r:255, g:215, b:0}`).
- The 5%-white glass over gold creates a bright warm glow, and the `#e0e0e0` text (light gray) has **very poor contrast** against that background — effectively making the UI unreadable.
- Since the canvas fills the full viewport, every glass panel becomes a gold-blur window.

---

## Implementation Plan

### Step 1: Explicit sunrise duration feedback in the UI
**File:** `static/index.html`

- Show a live countdown or progress indicator while sunrise is active so the user knows how long is left.
- Simple approach: update the button text from `☀️ Sunrise...` to `☀️ Sunrise... XXs` every second during the sequence.

### Step 2: Keep UI readable during sunrise
**File:** `static/index.html`

**Option A (Recommended — minimum change):**
- Add a CSS class `sunrise-active` on `<body>` when sunrise starts, remove it when it ends.
- In that class, strengthen the glass panel backgrounds to be more opaque during sunrise (e.g., `background: rgba(0, 0, 0, 0.6)` instead of `rgba(255,255,255,0.05)`).
- Keep `backdrop-filter: blur(...)` but the darker base will keep enough contrast.

```css
body.sunrise-active .glass {
  background: rgba(0, 0, 0, 0.6) !important;
}
body.sunrise-active #sunrise-settings {
  background: rgba(0, 0, 0, 0.6) !important;
}
body.sunrise-active .chaos-card {
  background: rgba(0, 0, 0, 0.6) !important;
}
```

- Toggle: `document.body.classList.add('sunrise-active')` / `.remove('sunrise-active')` in `startSunriseSequence()` and its cleanup timeout.

**Option B (more polished):**
- Instead of full gold, limit the sunrise effect to a subtle golden glow around the canvas edges (radial gradient overlay) and a golden tint on the glass panels, keeping the main canvas dark.
- This is more visually pleasing but a bigger change.

→ **Proceed with Option A** for v0.1 — functional fix with minimum risk.

### Step 3: Restore time-aware background after sunrise ends
**File:** `static/index.html`

- After the sunrise sequence completes and resets, re-fetch the time color (`fetchTimeColor()`) so the background returns to the appropriate blue/orange/purple for the current hour.
- This is already available as a function; just call it in the cleanup callback after `SUNRISE_DURATION + 200`.

### Step 4: Consider making duration configurable (optional stretch)
- Add a duration slider/input in the sunrise settings panel (15s / 30s / 60s / 120s).
- Store alongside the existing schedule settings.
- This is a nice-to-have; skip for now unless the user requests it.

---

## Files to Modify

| File | Change |
|---|---|
| `static/index.html` | Add `sunrise-active` CSS class for glass panels; add body class toggle in JS; add countdown on button; re-fetch time color after sunrise ends. |

## Tests

- No backend changes, so existing pytest suite remains unchanged.
- Manual verification: start sunrise → UI panels remain readable → check countdown shows seconds → after sequence completes → background returns to time-appropriate color.

## Risks

- Low. CSS-only change with JS class toggles. No backend impact.
- The `!important` flags in CSS are intentional to override inline/computed styles during the active state.
- Ensure the class is removed even if `startSunriseSequence()` throws (the catch block already has a `setTimeout` cleanup).

## Acceptance Criteria

1. Sunrise button shows a live countdown (e.g., `☀️ 42s`) during the 60-second sequence.
2. Glass panels (top bar, bottom section, cards, settings panel) remain readable throughout.
3. After the 60s sunrise ends, the canvas background transitions back to the time-aware color (not permanently gold).
4. Works for both manual click and auto-sunrise trigger.
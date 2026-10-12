# חיפוש גאוגרפי (Geo Search): Design System

Version 1.0 · Source: the "Geo Search — main screen" design canvas (1440 × 900 desktop frame)

The UI is a **quiet, neutral frame around a real, detailed map**. The panels stay white and gray so the map's own colors (water, parks, roads) are what draws the eye. A single **sky-blue accent**, used as a solid color or a gradient, marks anything the user owns or acts on: the selected area, their messages and primary actions. Search results use a separate **periwinkle** so they never blend with the accent or with map colors.

The product is **Hebrew-first and right-to-left (RTL)**.

---

## 1. Color tokens

### 1.1 Neutrals (UI frame)

| Token | Hex | Usage |
|---|---|---|
| `--color-bg-app` | `#F4F4F5` | App background behind cards (center column gutter) |
| `--color-surface` | `#FFFFFF` | Panels, cards, top bar, chat, inputs on surface |
| `--color-surface-subtle` | `#FAFAFA` | Result-card footer strip |
| `--color-surface-muted` | `#F1F1F3` | Tool icon tiles, icon buttons, close buttons, hover row in lists |
| `--color-surface-sunken` | `#F4F4F5` | Composer box, history search field |
| `--color-border` | `#E4E4E7` | Panel dividers, card borders, input borders on white |
| `--color-border-strong` | `#D4D4D8` | Secondary-button borders, chip outlines |
| `--color-divider-soft` | `#F0F0F2` | Chat header bottom border |
| `--color-divider-row` | `#F1F1F3` | Rows inside the result card |
| `--color-text` | `#18181B` | Primary text, headings, icons on light surfaces |
| `--color-text-secondary` | `#46464E` | Tool icons, step-chip text, secondary icon buttons |
| `--color-text-muted` | `#57575F` | Meta text, captions, timestamps, counts, placeholders |

Contrast (on `#FFFFFF`): `--color-text` 17.7:1 · `--color-text-secondary` 9.6:1 · `--color-text-muted` 7.3:1. All pass WCAG AA for body text.

### 1.2 Accent: sky blue

| Token | Value | Usage |
|---|---|---|
| `--color-accent` | `#2B84C9` | Polygon end-stop, links, active borders, wordmark "גאוגרפי", drawing-tool selection |
| `--color-accent-light` | `#5CC6F2` | Gradient start |
| `--color-accent-strong` | `#17609C` | Text and icons placed **on** accent-tinted backgrounds (active pills, the "מצולע 1" chip, "הצגת כל ה-8") |
| `--color-accent-soft` | `#2B84C926` (accent @ 15%) | Selected/active backgrounds: history item, active draw tool, tools toggle when open, chips, avatars |
| `--color-accent-fill` | `#2B84C929` (accent @ 16%) | Polygon fill fallback, history thumbnails |
| `--gradient-accent` | `linear-gradient(135deg, #5CC6F2 0%, #2B84C9 100%)` | Logo ring and handle, send button |
| `--gradient-accent-soft` | `linear-gradient(135deg, #5CC6F233 0%, #2B84C933 100%)` | User chat bubble (text stays `--color-text`) |

Polygon on the map: stroke = `--gradient-accent` (diagonal, light → accent), 3px, round joins. Fill = the same gradient at 22% → 30% opacity.

> **Rule:** never put white body text on `--color-accent`. At 3.9:1 it fails AA for normal text. White is allowed on the accent or gradient only for **icons** (the send arrow, the logo), which need 3:1. Text on accent-tinted backgrounds uses `--color-accent-strong`.

Approved alternative accents (offered as a design tweak): `#3D9BE0` (lighter), `#1F6FB0` (deeper), `#2F92B8` (blue-teal).

### 1.3 Results: periwinkle

| Token | Hex | Usage |
|---|---|---|
| `--color-result` | `#7D8BFA` | Result markers on the map (with a 3px `#FFFFFF` ring), logo pin |
| `--color-result-soft` | `#E8EBFF` | Number badge background in the result list |
| `--color-result-strong` | `#4150C4` | Number badge text (7.0:1 on soft) |

Do **not** use black, orange, coral or saturated purple for results. These were rejected in review.

### 1.4 Status

| Token | Hex | Usage |
|---|---|---|
| `--color-success` | `#2FA36B` | "מוכן" (ready) dot, check marks in tool-step chips |
| `--color-success-soft` | `#EAF5EF` | Tool-step chip background |
| `--color-danger` | `#B4372A` | Destructive actions: "ניקוי" (clear area). Text 5.9:1 on white |

### 1.5 Map placeholder palette (mock only)

These are used only by the illustrative SVG map in the mockup. **In production the real map tiles provide these colors.** Do not reuse them in UI chrome.

| Element | Hex |
|---|---|
| Land | `#F3F0EA` |
| City block / building | `#EAE6DF` / `#DFDBD3` |
| Water | `#AADAFF` |
| Beach | `#F3E6C0` |
| Park | `#C5E8C5` |
| Arterial road fill / casing | `#FCE7A2` / `#E8C35E` |
| Highway fill / casing | `#F8B84E` / `#E59A2E` |
| Rail, POI dots | `#9AA0A6` |
| Street labels | `#5F6368` with a 3px white halo |

### 1.6 Elevation

| Token | Value | Usage |
|---|---|---|
| `--shadow-float` | `0 2px 10px rgba(30,34,53,0.10)` | Floating controls on the map (search, zoom, base map, expand) |
| `--shadow-toolbar` | `0 4px 14px rgba(30,34,53,0.12)` | Area-drawing toolbar on the map |

Panels and cards use borders, **not** shadows.

---

## 2. Typography

### 2.1 Families

| Token | Stack | Usage |
|---|---|---|
| `--font-ui` | `'Rubik', system-ui, sans-serif` | Everything except coordinates. Rubik has full Hebrew support. |
| `--font-mono` | `'IBM Plex Mono', monospace` | Coordinate readout only |

Google Fonts: `Rubik:wght@400;500;600` and `IBM+Plex+Mono:wght@400;500`.

### 2.2 Scale

| Token | Size / line-height | Weight | Usage |
|---|---|---|---|
| `--text-brand` | 20px / 1.2, letter-spacing −0.01em | 600 | Wordmark "חיפוש **גאוגרפי**" (second word in `--color-accent`) |
| `--text-title` | 16px / 1.3 | 600 | Panel and section titles: היסטוריה, כלי הסוכן, הסוכן הגאוגרפי |
| `--text-body-lg` | 15px / 1.55 (agent text 1.65) | 400 | Chat messages, composer input |
| `--text-body` | 14px / 1.35 | 400 or 500 | Inputs, list item titles (500), result names (500), project name |
| `--text-label` | 13px / 1.5 | 400 or 500 | Buttons, chips, group headers (500), descriptions, tooltips |
| `--text-caption` | 12px / 1.4 | 400 | Timestamps, result counts, status line, coordinates, result-badge numbers (600) |

Rules:
- Only three weights: 400 regular, 500 medium (interactive labels and item titles), 600 semibold (titles and brand).
- No uppercase and no letter-spacing on Hebrew text. Group headers use 13px/500 in `--color-text-muted` instead.
- Numbers, units and Latin names inside Hebrew text stay inline. Use Hebrew unit abbreviations with geresh/gershayim: `מ׳` (meters), `קמ״ר` (km²).
- Coordinates are always rendered `dir="ltr"` in `--font-mono`: `32.0853° N, 34.7818° E`.

---

## 3. Spacing, sizing and shape

### 3.1 Spacing scale (px)

`2 · 4 · 6 · 8 · 10 · 12 · 14 · 16 · 18 · 20`

| Token | px | Typical use |
|---|---|---|
| `--space-1` | 2 | Gap between draw-toolbar buttons, list rows |
| `--space-2` | 4 | Toolbar inner padding |
| `--space-3` | 6 | Icon-to-label gap in buttons and chips, chip groups |
| `--space-4` | 8 | Button groups, list item inner padding |
| `--space-5` | 10 | Logo-to-text, list icon-to-text |
| `--space-6` | 12 | Map overlay inset, row gaps, panel header stack |
| `--space-7` | 14 | Center-column padding and gap between map and chat |
| `--space-8` | 16 | Panel horizontal padding |
| `--space-9` | 18 | Panel header top padding, gap between chat messages |
| `--space-10` | 20 | Chat horizontal padding, top-bar horizontal padding |

### 3.2 Radius

| Token | px | Usage |
|---|---|---|
| `--radius-xs` | 4 | (reserved) |
| `--radius-sm` | 11 | Logo tile, tool icon tiles |
| `--radius-md` | 12 | Tooltip description card |
| `--radius-lg` | 14 | History items, tool rows |
| `--radius-xl` | 16 | Result card |
| `--radius-2xl` | 20 | Map card, chat card |
| `--radius-composer` | 22 | Composer box, draw toolbar |
| `--radius-pill` | 9999 (height ÷ 2) | All buttons, chips, search fields, avatars |
| User bubble | `18px 18px 18px 6px` | The 6px "tail" corner is bottom-left (bubble sits on the left in RTL) |

### 3.3 Control sizes

| Control | Height | Horizontal padding | Notes |
|---|---|---|---|
| Map overlay button (icon) | 42 × 42 | — | Pill, `--shadow-float` |
| Map search field | 42 | 14 | Pill, width `min(340px, 100% − 200px)` |
| Send button (icon) | 42 × 42 | — | `--gradient-accent`, white arrow icon |
| History search field | 42 | 16 | Pill, `--color-surface-sunken` |
| Header buttons (tools toggle, new chat) | 38 | 14 | Pill, 1px border |
| Draw-toolbar button | 36 | 12 | Pill, no border |
| Icon close button | 36 × 36 | — | Pill, `--color-surface-muted` |
| Result card buttons | 34 | 14 | Pill |
| Suggestion chips | min 36 | 14 (6 vertical) | Pill, wraps |
| Composer chips (area, attach) | 32 | 12 | Pill |
| Account avatar | 40 × 40 | — | Circle |

Minimum touch target: 32px for inline chips and 36px or more for everything else. The primary actions (send, map controls) are 42px.

### 3.4 Icons

- Inline SVG, stroke style, `fill: none`, round caps and joins, stroke 1.8–2.4 depending on size.
- Sizes: 12–14 (in chips), 16 (buttons), 18 (titles and list tiles), 20 (empty).
- Icon-only buttons always carry a Hebrew `aria-label`. Decorative icons get `aria-hidden="true"`.
- No emoji.

---

## 4. Layout structure

Desktop reference frame: **1440 × 900**.

```
┌──────────────────────────────── Top bar · 60px ────────────────────────────────┐
│ [logo 40] חיפוש גאוגרפי  הפרויקט שלי                                [avatar 40] │
├──────────────┬─────────────────────────────────────────────────┬───────────────┤
│              │  padding 14 · gap 14                            │               │
│  HISTORY     │  ┌──────────── Map card · 300px ─────────────┐  │  AGENT TOOLS  │
│  (right)     │  │ search ▸ top-right      controls ◂ top-left │  │  (left)       │
│  flex:       │  │ draw toolbar ▸ bottom-right  coords ◂ b-left│  │  hidden by    │
│  1 1 300px   │  └────────────────────────────────────────────┘  │  default      │
│              │  ┌──────────── Chat card · fills rest ────────┐  │  flex:        │
│  840px tall  │  │ header (12/20)                              │  │  1 1 272px    │
│              │  │ messages (20) · column max 760, centered    │  │               │
│              │  │ composer (0 20 18) · max 760, centered      │  │               │
│              │  └────────────────────────────────────────────┘  │               │
└──────────────┴─────────────────────────────────────────────────┴───────────────┘
```

| Region | Spec |
|---|---|
| Root | `dir="rtl"`, `min-height: 100vh`, column flex, background `--color-bg-app` |
| Top bar | `min-height: 60px`, padding `8px 20px`, white, bottom border `--color-border`, wraps |
| Workspace row | `display: flex; flex-wrap: wrap; min-height: 840px` |
| History panel (DOM first → renders on the **right**) | `flex: 1 1 300px; max-height: 840px`, white, `border-left: 1px solid --color-border`. Header padding `18px 16px 14px`, list padding `0 10px 10px` |
| Center column | `flex: 999 1 560px; min-width: 0; max-height: 840px; padding: 14px; gap: 14px`, column flex |
| Map card | `flex: 0 0 300px; height: 300px`, radius 20, 1px border, `overflow: hidden`. Overlays are inset 12px |
| Chat card | `flex: 1 1 auto; min-height: 480px`, white, radius 20, 1px border. Messages scroll internally |
| Agent tools panel (DOM last → renders on the **left**) | `flex: 1 1 272px; max-height: 840px`, white, `border-right: 1px solid --color-border`. Rendered **only** when toggled open |

Physical placement is fixed by product decision: **history is on the right and tools are on the left**, even though the page is RTL.

Responsive: the row wraps. Below about 1132px of combined bases, the panels drop to full-width rows. The center column's `flex: 999` keeps nearly all extra width going to the map and chat. Map overlays wrap or shrink, and the search field width is `min(340px, 100% − 200px)`.

---

## 5. Components

### 5.1 Top bar
- Logo (40px SVG) · 10px · wordmark (20/600, "גאוגרפי" in accent) · 10px · project name (14, muted).
- Right edge: account avatar only (40px circle, `--color-surface-muted`). No Share and no Save-view actions.

### 5.2 Logo
A magnifying glass. The ring is a 12px-radius circle with a 3.4px stroke in `--gradient-accent`. The handle runs from (26,26) to (34.5,34.5) with a 5px stroke in the same gradient and a round cap. The lens (r 11) is clipped and shows a mini light map: background `#EEF5FB`, park `#CDEBC9`, river `#8FCDF4` at 3.2px, four white streets at 1.6px, and a center pin in `--color-result` with a white outline and white dot. viewBox `0 0 40 40`.

### 5.3 Map card
- **Search field** (top-right): pill, search icon, placeholder "חיפוש מקום או קואורדינטות".
- **Controls** (top-left, gap 6): expand, base map, and a zoom pill group (− | +).
- **Area drawing toolbar** (bottom-right): pill container, 4px padding. Buttons: מצולע, מלבן, עיגול, ציור חופשי, עריכה · divider (1 × 22px) · ניקוי (danger color). Active state: `--color-accent-soft` background with `--color-accent-strong` text. Exactly one is active, defaulting to מצולע.
- **Coordinates** (bottom-left): 12px mono, white at 90%, radius 14.
- **Polygon label**: white pill on the map reading "מצולע 1 · 2.4 קמ״ר".

### 5.4 Chat card
- **Header**: avatar (36 circle, accent-soft, accent icon) · title "הסוכן הגאוגרפי" · status "● מוכן · 11 כלים זמינים". Actions: **tools toggle** "כלי הסוכן · 11" (`aria-expanded`, `aria-controls="agent-tools"`; when open it shows the accent-soft background, accent border and accent-strong text) and "שיחה חדשה".
- **Agent message**: 30px circle avatar · column (gap 10) containing:
  1. Tool-step chips (success-soft, ✓ icon, tool name 500 · detail)
  2. Answer text (15/1.65)
  3. Optional result card: rows with padding `11px 16px` and a 26px number badge, plus a footer (`--color-surface-subtle`) with "מוצגים X מתוך Y", "ייצוא" and "הצגת כל ה-Y"
  4. Optional suggestion chips
- **User message**: aligned to the end (left in RTL), max-width 75%, `--gradient-accent-soft` background, dark text, padding `10px 16px`.
- **Composer**: sunken box with radius 22 and padding `12px 14px 10px`. It holds a 2-row textarea (no border, no resize), the area chip (removable, × at 24px), a "צירוף קובץ" chip, the hint "Enter לשליחה" and the send button. Enter sends; Shift+Enter adds a newline.

### 5.5 History panel
- Title row: clock icon in accent, "היסטוריה", and "N חיפושים" (muted).
- Pill search field filtering by query text.
- Day group labels (13/500, muted): היום · אתמול · a date.
- Item: full-width button, radius 14, padding 10. It shows a 46px thumbnail (radius 12, the polygon in accent), the query (14/500) and "HH:MM · N תוצאות" (12, muted). Selected: `--color-accent-soft` background with `aria-current`.
- Empty filter state: "אין חיפושים תואמים."
- No footer actions (no clear history, no saved areas).

### 5.6 Agent tools panel (on demand)
- Read-only. **No switches, no search, no "add tool".**
- Header: tool icon in accent, "כלי הסוכן", × close (36px). A one-line explainer below it.
- Groups (13/500 muted header): חיפוש · גאומטריה · נתונים ותצלומים.
- Row: 34px tile (`--color-surface-muted`, `--color-text-secondary` icon) followed by the Hebrew tool name (14/500).
- **Description appears only on hover or keyboard focus.** The row gets the `--color-surface-muted` background and expands to show a card below the name (white, 1px border, radius 12, padding `8px 12px`, 13/1.5, indented 44px from the start edge). Rows are focusable (`tabindex="0"`).

---

## 6. Interaction states

| State | Treatment |
|---|---|
| Hover (buttons) | Background one step darker (`#FFFFFF` → `--color-surface-muted`). Accent buttons: ~8% darker |
| Active or selected | `--color-accent-soft` background · `--color-accent-strong` text · optional 1px `--color-accent` border |
| Focus | Visible 2px outline in `--color-accent`, offset 2–4px. Never remove it |
| Disabled | 40% opacity, `cursor: not-allowed` |
| Agent working | Tool-step chip with detail "רץ על …" and the placeholder answer "עובד על זה…" |

Motion: keep it subtle. 150ms ease-out for hover and expand, 200ms for panel open and close. Respect `prefers-reduced-motion`.

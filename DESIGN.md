# zd-agent Admin Design System

## 1. Atmosphere & Identity

The admin console should feel like a calm Dify control room: bright, precise, and operational. The signature is a soft blue command surface, with Dify blue used only for brand, active navigation, focus, and primary actions. Information density is allowed, but hierarchy must stay clear through spacing, tonal surfaces, and tabular alignment.

## 2. Color

### Palette

| Role | Token | Light | Usage |
|------|-------|-------|-------|
| Brand primary | --brand | #0033FF | Dify brand, primary CTA, active nav |
| Brand hover | --brand-hover | #1746FF | Hover and pressed states |
| Brand soft | --brand-soft | #EEF4FF | Selected surface, subtle highlight |
| Background | --bg | #F5F8FF | App canvas |
| Background wash | --bg-wash | #EAF3FF | Sidebar and login atmosphere |
| Surface | --surface | #FFFFFF | Cards, forms, modal bodies |
| Surface raised | --surface-raised | #FBFDFF | Secondary panels and details |
| Surface blue | --surface-blue | #F2F7FF | Blue-tinted detail regions |
| Text primary | --text | #0B1F3A | Headings and body |
| Text secondary | --muted | #5E6F8C | Hints, metadata |
| Text tertiary | --faint | #8796AF | Disabled and quiet metadata |
| Border | --border | #D9E6F7 | Cards and dividers |
| Border strong | --border-strong | #BBD0EC | Inputs and controls |
| Success | --success | #14813D | Healthy state |
| Warning | --warn | #A04E0A | Pending or caution state |
| Danger | --danger | #D92D20 | Error state |

### Rules

- Blue is functional, not decorative. Use it for identity, state, focus, and primary actions.
- Text must stay high-contrast on light surfaces.
- Status colors appear only in badges, metrics, and event dots.

## 3. Typography

### Scale

| Level | Size | Weight | Line Height | Usage |
|-------|------|--------|-------------|-------|
| Page title | 28px | 800 | 1.2 | Main page heading |
| Section title | 17px | 760 | 1.35 | Card and panel headers |
| Metric | 30px | 820 | 1.1 | KPI values |
| Body | 14px | 450 | 1.55 | Default UI text |
| Label | 13px | 720 | 1.4 | Form labels, table headers |
| Caption | 12px | 650 | 1.4 | Pills, metadata |
| Mono | 12px | 500 | 1.55 | JSON, paths, logs |

### Font Stack

- Primary: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
- Mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace

## 4. Spacing & Layout

### Base Unit

All spacing derives from a base of 4px.

| Token | Value | Usage |
|-------|-------|-------|
| --space-2 | 8px | Tight inline gaps |
| --space-3 | 12px | Control padding |
| --space-4 | 16px | Compact cards |
| --space-5 | 20px | Card body |
| --space-6 | 24px | Page groups |
| --space-8 | 32px | Major regions |
| --space-10 | 40px | Login shell and wide regions |

### Grid

- Sidebar width: 280px on desktop.
- Main content max width: 1680px.
- Cards use responsive grids: 4 columns on desktop, 2 on tablet, 1 on mobile.
- Data tables may overflow horizontally, but content must not overlap.

## 5. Components

### Auth shell

- Structure: split layout with a brand panel and an auth form panel.
- States: login, password change, SSO unavailable hint, error message.
- Accessibility: visible labels, autocomplete, keyboard focus rings.
- Motion: mount fade/translate, reduced motion disables transition.

### Button

- Variants: primary, secondary, SSO, danger, compact, icon.
- States: default, hover, active, focus, disabled.
- Primary uses --brand and --brand-hover.

### Sidebar

- Structure: brand lockup, nav list, health footer.
- States: active nav, hover nav, responsive top section on mobile.
- Active item uses brand blue with subtle shadow.

### Card and metric

- Structure: header plus body, or metric label/value/description.
- Radius: 14px for large panels, 12px for compact cards.
- Depth: mixed tonal surfaces and soft blue-tinted shadow.

### Run table

- Structure: horizontally scrollable table with fixed minimum row grid.
- States: collapsed row, expanded row, detail timeline, empty state.
- Table cells must truncate instead of overlap.

### Drawer and modal

- Structure: fixed overlay, raised surface, sticky header, scrollable body.
- States: open, close, Escape close.
- Backdrop uses a blue-tinted blur for focus isolation.

## 6. Motion & Interaction

| Type | Duration | Easing | Usage |
|------|----------|--------|-------|
| Micro | 140ms | ease-out | Buttons, nav hover |
| Standard | 220ms | ease-in-out | Drawer, modal, card hover |
| Emphasis | 360ms | cubic-bezier(0.16, 1, 0.3, 1) | Auth shell entry |

Rules:

- Animate transform, opacity, and color only.
- Every clickable element must have hover and focus states.
- Respect prefers-reduced-motion.

## 7. Depth & Surface

Strategy: mixed tonal-shift with restrained shadow.

| Level | Value | Usage |
|-------|-------|-------|
| Subtle | 0 1px 2px rgba(20, 54, 105, 0.05) | Rows and small controls |
| Card | 0 12px 32px rgba(31, 68, 122, 0.08) | Cards and panels |
| Prominent | 0 24px 70px rgba(14, 48, 105, 0.16) | Auth card, drawer, modal |

Large empty surfaces must use a tonal wash or subtle grid texture, not a flat blank fill.

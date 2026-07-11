# zd-agent Admin Design System

## 1. Product Character

The admin console is an agent control plane, not a marketing site. It should feel calm, precise, and trustworthy during repeated operational work. The visual language is bright and compact: white work surfaces, a pale-blue navigation rail, dark ink text, and Dify blue reserved for current state, focus, and primary commands.

The login screen uses the same control-plane identity. It presents one focused authentication surface instead of a promotional split hero. The authenticated shell prioritizes navigation clarity, scan speed, and stable geometry across every module.

## 2. Color Tokens

| Role | Token | Value | Usage |
|------|-------|-------|-------|
| Brand | `--brand` | `#155EEF` | Primary actions, active navigation, focus |
| Brand strong | `--brand-strong` | `#004EEB` | Pressed state |
| Brand soft | `--brand-soft` | `#EAF2FF` | Selected and highlighted surfaces |
| Canvas | `--canvas` | `#F4F7FB` | Main application background |
| Navigation | `--nav-bg` | `#EDF5FF` | Desktop sidebar and mobile drawer |
| Surface | `--surface` | `#FFFFFF` | Panels, forms, dialogs |
| Surface quiet | `--surface-quiet` | `#F8FAFD` | Secondary rows and grouped controls |
| Text | `--ink` | `#13213A` | Headings and primary content |
| Text secondary | `--ink-muted` | `#60708A` | Descriptions and metadata |
| Text tertiary | `--ink-faint` | `#8A98AD` | Disabled and low-priority metadata |
| Border | `--line` | `#D9E3F0` | Panel and row boundaries |
| Border strong | `--line-strong` | `#BDCCE0` | Inputs and active controls |
| Success | `--success` | `#16803F` | Healthy and successful states |
| Warning | `--warning` | `#A15C07` | Delayed and caution states |
| Danger | `--danger` | `#D92D20` | Errors and destructive actions |

Blue must remain functional. Large blue fields, decorative gradients, colored blobs, and one-hue page composition are not part of this system.

## 3. Typography

Primary stack: `Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.

| Role | Size | Weight | Line height |
|------|------|--------|-------------|
| Page title | 24px | 760 | 1.25 |
| Auth title | 24px | 760 | 1.25 |
| Section title | 16px | 700 | 1.4 |
| Metric | 28px | 760 | 1.15 |
| Body | 14px | 450 | 1.55 |
| Label | 13px | 650 | 1.4 |
| Caption | 12px | 550 | 1.45 |
| Mono | 12px | 500 | 1.55 |

Letter spacing is always `0`. Compact panels never use display-scale typography.

## 4. Layout

- Base spacing unit: 4px.
- Desktop sidebar: 248px fixed width.
- Desktop page header: 72px minimum height, sticky below the viewport edge.
- Main content: fluid width with 24px desktop gutters and a 1680px maximum working area.
- Login panel: 440px maximum width, vertically centered with a small brand header.
- Cards and panels: maximum 8px radius.
- Inputs and buttons: 6px radius, 40px standard height.
- Data tables retain stable grid tracks and scroll horizontally when required.
- Below 900px the sidebar becomes an overlay drawer; page content remains full width.
- Below 560px authentication actions and page-header controls stack without overflow.

## 5. Components

### Authentication panel

- One white panel containing brand, title, SSO actions, credentials form, and feedback.
- Password controls include a visible label, browser autocomplete, and a visibility toggle.
- SSO actions are full-width rows on small screens and two equal columns on wider screens.
- Login and password-change states share the same visual frame.
- Errors reserve layout space and use `role="alert"`.

### Navigation shell

- Sidebar contains brand lockup, grouped navigation, and a compact service-status footer.
- Groups are `工作台`, `Agent 配置`, and `系统`.
- Each destination has a Lucide-style line icon and text label.
- Active navigation uses a soft-blue background, blue icon, and dark text rather than a large solid-blue block.
- Mobile navigation uses a menu button, backdrop, focus-safe drawer, and closes after selection.

### Page header

- Sticky white surface with title, subtitle, live health indicator, and page actions.
- The title block and action block have stable layout boundaries.
- Action buttons wrap only when required; mobile stacks them below the title.

### Controls

- Primary button: solid brand blue, white text.
- Secondary button: white or quiet surface, strong border.
- Icon button: square 36px control with an accessible label and tooltip where meaning is unfamiliar.
- Every control has hover, active, focus-visible, disabled, loading, and error behavior.

### Content surfaces

- Panels use white surfaces, 1px borders, and restrained shadows only for elevation.
- Repeated item cards may use 8px radius; full page sections remain unframed.
- Cards are never nested inside decorative cards.
- Metrics, filters, tables, configuration groups, drawers, and dialogs reuse the same color and spacing tokens.

## 6. Motion and Accessibility

- Micro interactions: 140ms ease-out.
- Drawer and dialog transitions: 200ms ease-out using only transform and opacity.
- No decorative animation.
- `prefers-reduced-motion: reduce` removes nonessential transitions.
- Focus rings use a 2px brand outline with 2px offset.
- Text and controls meet WCAG AA contrast.
- All forms use explicit labels; icon buttons use accessible names; current navigation uses `aria-current="page"`.

## 7. Depth

Depth is primarily tonal and border-based.

| Level | Value | Usage |
|-------|-------|-------|
| Control | `0 1px 2px rgba(15, 35, 65, 0.05)` | Inputs and buttons |
| Panel | `0 8px 24px rgba(28, 55, 92, 0.07)` | Auth panel and raised content |
| Overlay | `0 20px 48px rgba(15, 35, 65, 0.16)` | Drawer and modal |

The page canvas stays quiet. Empty regions use spacing and subtle tonal separation rather than gradients, textures, or decoration.

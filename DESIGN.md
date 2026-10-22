---
version: alpha
name: Multi-Agent Workspace
description: A quiet coding workspace for a local master model and remote specialist agents.
colors:
  canvas: "#191A1C"
  sidebar: "#141517"
  surface: "#222326"
  elevated: "#2B2D30"
  border: "#333539"
  primary: "#ECEDEE"
  secondary: "#BEC0C5"
  muted: "#95989F"
  accent: "#A5C9B0"
  on-accent: "#17251B"
  error: "#F0A8A8"
  online: "#6FCF8E"
  light-canvas: "#FAFAFA"
  light-sidebar: "#F1F2F3"
  light-surface: "#FFFFFF"
  light-elevated: "#E6E8EA"
  light-border: "#D8DBDE"
  light-primary: "#202225"
  light-secondary: "#4B5058"
  light-muted: "#626872"
  light-accent: "#345D40"
  light-on-accent: "#FFFFFF"
  light-error: "#A52C36"
  light-online: "#1F7A43"
typography:
  headline-lg: { fontFamily: Segoe UI, fontSize: 36px, fontWeight: 500, lineHeight: 1.2, letterSpacing: -0.04em }
  headline-md: { fontFamily: Segoe UI, fontSize: 22px, fontWeight: 500, lineHeight: 1.35, letterSpacing: -0.025em }
  body-lg: { fontFamily: Segoe UI, fontSize: 15px, fontWeight: 400, lineHeight: 1.7 }
  body-md: { fontFamily: Segoe UI, fontSize: 14px, fontWeight: 400, lineHeight: 1.6 }
  body-sm: { fontFamily: Segoe UI, fontSize: 13px, fontWeight: 400, lineHeight: 1.5 }
  label-lg: { fontFamily: Segoe UI, fontSize: 14px, fontWeight: 500, lineHeight: 1.4 }
  label-md: { fontFamily: Segoe UI, fontSize: 12px, fontWeight: 500, lineHeight: 1.5 }
  label-sm: { fontFamily: Segoe UI, fontSize: 11px, fontWeight: 500, lineHeight: 1.5 }
  code: { fontFamily: Cascadia Code, fontSize: 12px, fontWeight: 400, lineHeight: 1.65 }
rounded: { sm: 6px, md: 9px, lg: 14px, xl: 18px, full: 999px }
spacing: { xs: 4px, sm: 8px, md: 12px, lg: 16px, xl: 24px, xxl: 32px, section: 48px }
components:
  composer: { backgroundColor: "{colors.surface}", rounded: "{rounded.xl}", padding: 16px }
  button-primary: { backgroundColor: "{colors.primary}", textColor: "{colors.canvas}", rounded: "{rounded.md}", height: 34px }
  navigation-active: { backgroundColor: "{colors.surface}", textColor: "{colors.primary}", rounded: "{rounded.sm}" }
  panel: { backgroundColor: "{colors.sidebar}", padding: 20px }
---

# Multi-Agent Workspace

## Overview
A desktop coding workspace inspired by the panel composition and calm visual hierarchy of Codex and Antigravity. This is an original interface, not an official implementation of either product. Use the existing Django templates, native CSS, and JavaScript. Design variance 4, motion intensity 2, visual density 6.

The workspace uses Django APIs and PostgreSQL for projects, conversations, model configuration, and endpoints. Browser storage contains appearance and selection preferences. Offer an explicit, idempotent import for older browser-local records, preserving them until the database transaction succeeds.

## Colors
Dark mode is the default. Use neutral charcoal layers and hairline borders. The muted green accent identifies the master model and focused controls. Semantic errors use the error token. Light mode swaps all surface and text tokens together. Never present configured endpoints as verified online agents.

## Typography
Use the system Segoe UI stack for an offline-first Windows workspace, with platform sans-serif fallbacks. Use Cascadia Code and monospace fallbacks for identifiers, shortcuts, and code. Reserve the large headline for the empty conversation. Most interface text is 12–14px.

## Layout
Desktop: 240px persistent sidebar, flexible conversation area, optional 288px agent panel. A compact header and bottom status bar frame the workspace. Limit conversation and composer width to 760px. Below 1180px, make the agent panel an overlay; below 760px, make the sidebar an accessible modal drawer. Support 360px-wide screens and short viewports without hiding the composer.

## Elevation & Depth
Use tonal separation and borders for persistent panels. Reserve shadows for dialogs and popovers. No gradients, decorative background imagery, or fabricated activity.

## Shapes
Navigation uses 6px corners, controls 9px, dialogs 14px, and the composer 18px. Icons come from the locally vendored Tabler outline family with consistent strokes.

## Components
- Sidebar: new chat, search, conversation history, agent access, and settings.
- Conversation: concise empty state, useful prompt starters, readable user messages, code blocks, visible pending and error states.
- Composer: auto-growing textarea, explicit master-model selection, send/stop action, and keyboard guidance.
- Agent panel: local master plus up to five configured remote models; distinguish configuration from live health. Specialists show an Online/Offline label with a status dot, using the online token only when the server reports a heartbeat within the timeout; the label never relies on color alone.
- Settings: editable API connections and model roles backed by Django. Keep saved API keys on the server and provide an explicit clear-key action. An unconfigured master remains visibly unconfigured.
- Dialogs: labelled controls, explicit close actions, focus containment, Escape handling, and restored focus.
- Task plans: a project-scoped dialog with a request form, saved plan history, and readable task details. Show dependencies, capabilities, outputs, and acceptance criteria; distinguish saved pending tasks from execution. Reuse existing surface, border, type, and spacing tokens.

## Do's and Don'ts
- Capture the originating conversation and request ID when messages are sent. Reload authoritative saved messages after each response.
- Store user-generated text safely and render code as text, never executable HTML.
- Clearly identify unavailable orchestration, test execution, and remote-health capabilities.
- Provide keyboard shortcuts, visible focus, reduced motion, and accessible names for icon controls.
- Do not seed fake projects, connected machines, running tasks, or success metrics.
- Do not require remote fonts, CDN scripts, or a frontend framework migration.

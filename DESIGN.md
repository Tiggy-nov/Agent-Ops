---
name: Hoistway Audit
description: A precise technical instrument for evaluating redundancy in production agent fleets.
colors:
  instrument-black: "#090c11"
  rail-black: "#0c1016"
  panel: "#10151d"
  panel-raised: "#151b24"
  panel-hover: "#19212c"
  border: "#252e3a"
  border-strong: "#344154"
  primary-text: "#eef3fa"
  secondary-text: "#909cac"
  quiet-text: "#8592a3"
  audit-blue: "#7b9cff"
  pass-green: "#73d2a0"
  kill-red: "#ff8178"
  collecting-amber: "#e7bd66"
typography:
  display:
    fontFamily: "Hoistway Plex, Avenir Next, Helvetica Neue, sans-serif"
    fontSize: "clamp(28px, 3vw, 38px)"
    fontWeight: 680
    lineHeight: 1.15
    letterSpacing: "-0.04em"
  title:
    fontFamily: "Hoistway Plex, Avenir Next, Helvetica Neue, sans-serif"
    fontSize: "15px"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.015em"
  body:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  measurement:
    fontFamily: "SFMono-Regular, Consolas, Liberation Mono, monospace"
    fontSize: "20px"
    fontWeight: 500
    lineHeight: 1
  label:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "10px"
    fontWeight: 650
    lineHeight: 1.4
    letterSpacing: "0.055em"
rounded:
  control: "8px"
  panel: "12px"
  status: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "18px"
  xl: "24px"
  page: "40px"
components:
  button-primary:
    backgroundColor: "#354fa6"
    textColor: "#f4f7ff"
    rounded: "{rounded.control}"
    padding: "0 13px"
    height: "36px"
  panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.primary-text}"
    rounded: "{rounded.panel}"
    padding: "18px 20px"
  status-pass:
    backgroundColor: "rgba(115, 210, 160, 0.11)"
    textColor: "{colors.pass-green}"
    rounded: "{rounded.status}"
    padding: "3px 7px"
  status-kill:
    backgroundColor: "rgba(255, 129, 120, 0.11)"
    textColor: "{colors.kill-red}"
    rounded: "{rounded.status}"
    padding: "3px 7px"
---

# Design System: Hoistway Audit

## Overview

**Creative North Star: "The Flight Recorder"**

Hoistway is a precise, dark technical instrument. It should feel like a trustworthy console an infrastructure team keeps open during a production audit: dense enough to expose the evidence, calm enough to support decisions, and conservative about what has actually been measured.

The interface leads with the decision, follows with the evidence, and ends with the method. Visual emphasis tracks epistemic confidence. Blue indicates interface state, while green, red and amber are reserved for measured outcomes.

**Key Characteristics:**

- Verdict-first hierarchy with dense evidence beneath it.
- Flat cool-neutral surfaces separated by restrained borders.
- Distinct technical display lettering and monospace measurements.
- Compact controls and explicit audit guardrails.
- Responsive reflow without deleting evidence columns.

## Colors

The palette is a cool, low-luminance instrument panel with one interface accent and three strictly semantic outcome colours.

### Primary

- **Audit Blue:** Used for progress, focus and current navigation. It never implies success.

### Neutral

- **Instrument Black:** The application canvas.
- **Rail Black:** The persistent navigation rail.
- **Panel:** The default evidence surface.
- **Primary Text:** High-confidence labels, decisions and headings.
- **Secondary Text:** Supporting explanation.
- **Quiet Text:** Microcopy, fingerprints and table metadata.

### Named Rules

**The Evidence Colour Rule.** Green, red and amber appear only when the product is communicating a measured state.

**The One Accent Rule.** Blue is the only non-semantic interface accent.

## Typography

**Display Font:** Hoistway Plex with Avenir Next and Helvetica Neue fallbacks  
**Body Font:** Native interface sans stack  
**Label/Mono Font:** SFMono-Regular with Consolas and Liberation Mono fallbacks

**Character:** Display lettering is restrained and technical, with enough character to distinguish the product from a generic admin template. Measurements and identifiers use monospace so columns scan predictably.

### Hierarchy

- **Display:** Used once for the audit title.
- **Title:** Used for section headings, the brand and verdict labels.
- **Body:** Used for explanations and navigation.
- **Measurement:** Used for rates, durations, counts and fingerprints.
- **Label:** Used for compact table headers and metadata.

### Named Rules

**The Measurement Voice Rule.** Numbers and identifiers use monospace; prose does not.

## Layout

Desktop uses a 228px persistent rail and a flexible evidence workspace. The main page has a maximum width of 1560px and 40px horizontal padding. The summary uses a three-part decision surface, followed by a four-column coverage strip, a repetition breakdown and a two-column evidence area with a 326px decision rail.

Below 1180px, the decision rail moves beneath the main tables. Below 900px, the sidebar becomes a compact top navigation. Below 720px, decision and metric grids stack or reduce to two columns. Evidence tables remain complete and scroll horizontally inside labelled, keyboard-focusable regions.

## Elevation & Depth

The system uses no decorative shadows. Depth comes from tonal separation, one-pixel borders and sticky application chrome. Surfaces remain flat because the page is a measurement instrument, not a layered workspace.

**The Flat Instrument Rule.** Use tonal layering and borders for structure; do not add ambient shadows or glass effects.

## Shapes

Evidence panels use restrained 12px corners. Controls use 8px corners. Status labels use a fully rounded pill because they are compact categorical states, not containers. The Hoistway mark is rigid orthogonal geometry.

## Components

### Buttons

- **Shape:** Compact control corners (8px).
- **Primary:** Muted blue surface with light text and a 36px minimum height.
- **Hover / Focus:** Slightly brighter blue on hover and a two-pixel blue focus ring with offset.

### Status labels

- **Style:** Transparent semantic tint, one-pixel semantic border and uppercase compact type.
- **State:** Green for PASS or eligible, red for KILL or excluded, amber for collection in progress.

### Cards / Containers

- **Corner Style:** Gently rounded evidence panels (12px).
- **Background:** Panel neutral over instrument black.
- **Shadow Strategy:** None.
- **Border:** One-pixel cool-neutral border.
- **Internal Padding:** Usually 18px to 26px, reduced inside dense evidence rows.

### Navigation

Navigation is quiet by default. The active destination gains a soft blue field and a two-pixel blue marker on desktop. Mobile removes the marker and preserves the blue field while allowing horizontal overflow.

### Evidence tables

Tables use compact uppercase headers, quiet metadata and tabular measurements. Rows gain only a subtle tonal response on hover. Narrow screens retain every column behind an explicit horizontal-scroll cue.

## Do's and Don'ts

### Do:

- **Do** lead with the measured decision and the evidence needed to trust it.
- **Do** reserve semantic colours for actual audit state.
- **Do** keep technical values aligned and scannable with monospace type.
- **Do** expose empty, loading, error and incomplete-audit states.
- **Do** preserve all evidence on small screens through labelled horizontal scrolling.

### Don't:

- **Don't** imply that Hoistway caches, modifies or controls traffic.
- **Don't** use gradients, glass effects, decorative charts or invented operational data.
- **Don't** use status pills as decoration.
- **Don't** hide guardrails or canonicalisation assumptions.
- **Don't** loosen density until evidence relationships become harder to compare.

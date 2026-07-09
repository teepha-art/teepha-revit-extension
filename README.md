# Teepha.extension

A [pyRevit](https://github.com/pyrevitlabs/pyRevit) extension that adds a set of schedule-building buttons for architectural and structural documentation. Pick one or more sheets, and each button builds the matching schedule (creating it if needed, reusing it if it already exists) and drops it onto the sheet for you.

## What's included

All buttons live under **Teepha tab → Schedules panel → Schedules pulldown**:

| Button | Creates |
|---|---|
| **Windows** | Window schedule (Level, Mark, Width, Height, Count), filtered per sheet's level |
| **Doors** | Door schedule (Level, Mark, Width, Height, Count), filtered per sheet's level |
| **Columns** | Structural column schedule (Mark, Base Level, Size, Count) |
| **Beams** | Structural framing (beam) schedule (Mark, Type Mark, Reference Level, Count) |
| **Footings** | Structural foundation schedule (Mark, Level, Type Mark, Count) — pad and strip footings together |
| **Slabs** | Floor (slab) schedule (Type, Thickness, Area, Level), split by structural/architectural discipline |
| **Sheet List** | Project-wide sheet list (Sheet Number, Sheet Name, Sheet Group), sorted and grouped |

Each schedule is grouped/sorted to a consistent house style and stacks automatically below any schedule already placed on the same sheet, so nothing overlaps.

## Requirements

- **pyRevit** 4.8 or later
- **Revit** 2021 or later

## Installation

1. Download or clone this repository somewhere on your machine, e.g.:
   ```
   git clone https://github.com/teepha-art/teepha-revit-extension.git
   ```
2. Open pyRevit's **Settings** (pyRevit tab → Settings, or run `pyrevit settings`).
3. Under **Custom Extension Directories**, click **Add Folder** and point it at the folder that *contains* `Teepha.extension` (not the `Teepha.extension` folder itself).
4. Click **Save Settings & Reload**. The **Teepha** tab should appear in the Revit ribbon.

Alternatively, from the command line with the [pyRevit CLI](https://github.com/pyrevitlabs/pyRevit):
```
pyrevit extend ui Teepha.extension https://github.com/teepha-art/teepha-revit-extension.git
```

If you'd rather not keep a live git folder around, you can also just copy the `Teepha.extension` folder directly into pyRevit's `%APPDATA%\pyRevit\Extensions` directory and reload — no registration step needed.

## Prerequisites for your project

These buttons assume a few naming conventions from your project:

- **Levels named with `F.F.L`** (Finished Floor Level) — Doors and Windows prefer the F.F.L-named plan on a sheet over other plans when resolving which level to filter by.
- **Setting-out sheets with `SETTING` in the name** (e.g. "SETTING OUT", "SETTING-OUT") — Doors and Windows only offer these sheets when picking a target.
- **Floor types prefixed `NEO_STR_` / `NEO_ARC_`** — the Slabs button filters structural vs. architectural floors by these type-name prefixes.
- **A "Sheet Group" project parameter** should be present — the Sheet List button groups by it. If it's missing, this column is skipped gracefully rather than causing an error.

## Credit

Built by **Teepha**.

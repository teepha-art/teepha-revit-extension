# -*- coding: utf-8 -*-
"""Doors schedule builder for the Schedules pulldown."""

__title__ = "Doors"
__author__ = "Teepha"
__doc__ = ("Create a Door schedule (Level, Mark, Width, Height, Count), grouped "
           "and sorted by Mark, filtered to each selected sheet's level, and place "
           "it on that sheet.")

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewSheet, ViewSchedule, ViewType, BuiltInCategory,
    ElementId, Transaction, ScheduleFieldType, ScheduleSortGroupField,
    ScheduleSortOrder, ScheduleFilter, ScheduleFilterType, ScheduleSheetInstance,
    ScheduleFieldDisplayType, FormatOptions, UnitTypeId, XYZ,
)

doc = revit.doc
output = script.get_output()

# --- Category-specific config (mirrors the Windows button; only this block differs) -
CATEGORY = BuiltInCategory.OST_Doors
NAME_PREFIX = "DOORS"

# BuiltInParameter ids for the columns, in the exact house-style order.
PID_LEVEL = -1002062   # Level (instance)
PID_MARK = -1001203    # Mark  (instance)
PID_WIDTH = -1001301   # Width (type)
PID_HEIGHT = -1001300  # Height (type)


def eid_value(element_id):
    """ElementId's integer value across Revit versions (.Value new, .IntegerValue old)."""
    val = getattr(element_id, "Value", None)
    if val is None:
        val = element_id.IntegerValue
    return int(val)


def get_plan_level_for_sheet(sheet):
    """Return the Level of the floor plan on the sheet, preferring an F.F.L plan.

    A setting-out sheet may carry both a C.F.L and an F.F.L plan. Schedules are
    keyed to F.F.L, so an F.F.L plan wins. Falls back to the first floor plan's
    level when no F.F.L plan is present (e.g. flat roof, which has no F.F.L pair)."""
    plan_levels = []
    for vp_id in sheet.GetAllViewports():
        view = doc.GetElement(doc.GetElement(vp_id).ViewId)
        if view is not None and view.ViewType == ViewType.FloorPlan:
            gen_level = getattr(view, "GenLevel", None)
            if gen_level:
                plan_levels.append(gen_level)
    for lvl in plan_levels:
        if "F.F.L" in lvl.Name:
            return lvl
    return plan_levels[0] if plan_levels else None


def is_setting_out_sheet(sheet):
    """Schedules belong on SETTING-OUT sheets only, and only where a plan (hence a
    level) can be resolved. Matches 'SETTING OUT' and 'SETTING-OUT'; excludes any
    setting sheet with no floor plan (e.g. structural column/foundation sheets)."""
    return "SETTING" in sheet.Name.upper() \
        and get_plan_level_for_sheet(sheet) is not None


def find_schedule_by_name(name):
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if not vs.IsTemplate and vs.Name == name:
            return vs
    return None


def is_placed_on_sheet(schedule_id, sheet_id):
    for inst in FilteredElementCollector(doc).OfClass(ScheduleSheetInstance):
        if inst.ScheduleId == schedule_id and inst.OwnerViewId == sheet_id:
            return True
    return False


def build_schedule(level, name):
    """Create the door schedule filtered to *level*, house-style formatted."""
    vs = ViewSchedule.CreateSchedule(doc, ElementId(CATEGORY))
    vs.Name = name
    sdef = vs.Definition

    by_pid = {}
    count_field = None
    for sf in sdef.GetSchedulableFields():
        by_pid[eid_value(sf.ParameterId)] = sf
        if sf.FieldType == ScheduleFieldType.Count:
            count_field = sf

    f_level = sdef.AddField(by_pid[PID_LEVEL])
    f_mark = sdef.AddField(by_pid[PID_MARK])
    f_width = sdef.AddField(by_pid[PID_WIDTH])
    f_height = sdef.AddField(by_pid[PID_HEIGHT])
    f_count = sdef.AddField(count_field)

    # Width/Height: metres, 2 decimal places.
    metres_2dp = FormatOptions(UnitTypeId.Meters)
    metres_2dp.Accuracy = 0.01
    f_width.SetFormatOptions(metres_2dp)
    f_height.SetFormatOptions(metres_2dp)

    # Filter to this level only.
    sdef.AddFilter(ScheduleFilter(f_level.FieldId, ScheduleFilterType.Equal, level.Id))

    # Group + sort by Mark, collapse identical marks, grand total row.
    sdef.AddSortGroupField(
        ScheduleSortGroupField(f_mark.FieldId, ScheduleSortOrder.Ascending))
    # Grand total row: sum the Count column (total elements), labelled "Grand total".
    if f_count.CanTotal:
        f_count.DisplayType = ScheduleFieldDisplayType.Totals
    sdef.IsItemized = False
    sdef.ShowGrandTotal = True
    sdef.ShowGrandTotalTitle = True
    sdef.ShowGrandTotalCount = False
    return vs


def place_schedule(schedule, sheet):
    """Place the schedule at the top-left, stacked below any schedule already on
    the sheet so multiple schedules (e.g. Windows and Doors) don't overlap."""
    outline = sheet.Outline
    left = outline.Min.U + 0.05
    top = outline.Max.V - 0.05
    gap = 0.02  # ~6 mm between stacked schedules
    for inst in FilteredElementCollector(doc).OfClass(ScheduleSheetInstance):
        if inst.OwnerViewId == sheet.Id:
            box = inst.get_BoundingBox(sheet)
            if box is not None:
                top = min(top, box.Min.Y - gap)
    ScheduleSheetInstance.Create(doc, sheet.Id, schedule.Id, XYZ(left, top, 0.0))


def run():
    sheets = forms.select_sheets(
        title="Select SETTING-OUT sheets for Door schedules",
        button_name="Build door schedules",
        filterfunc=is_setting_out_sheet)
    if not sheets:
        script.exit()

    created, skipped, notes = [], [], []

    t = Transaction(doc, "Door schedules per sheet")
    t.Start()
    try:
        for sheet in sheets:
            tag = "%s - %s" % (sheet.SheetNumber, sheet.Name)
            level = get_plan_level_for_sheet(sheet)
            if level is None:
                skipped.append("%s -> no floor plan / no level found" % tag)
                continue

            name = "%s - %s" % (NAME_PREFIX, level.Name)
            existing = find_schedule_by_name(name)
            if existing is not None:
                choice = forms.alert(
                    "A schedule named '%s' already exists.\nWhat should I do?" % name,
                    options=["Reuse existing", "Create a new copy", "Skip this sheet"])
                if choice == "Skip this sheet" or not choice:
                    skipped.append("%s -> skipped (schedule exists)" % tag)
                    continue
                if choice == "Reuse existing":
                    if is_placed_on_sheet(existing.Id, sheet.Id):
                        notes.append("%s -> already had '%s' placed" % (tag, name))
                    else:
                        place_schedule(existing, sheet)
                        created.append("%s -> reused '%s'" % (tag, name))
                    continue
                # Create a new copy -> unique name
                suffix = 2
                while find_schedule_by_name("%s (%d)" % (name, suffix)):
                    suffix += 1
                name = "%s (%d)" % (name, suffix)

            schedule = build_schedule(level, name)
            place_schedule(schedule, sheet)

            door_count = len(FilteredElementCollector(doc, schedule.Id)
                             .WhereElementIsNotElementType().ToElementIds())
            tail = "" if door_count else "  (no doors on this level - empty schedule)"
            created.append("%s -> '%s' (%d doors)%s" % (tag, name, door_count, tail))
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    report_results(created, skipped, notes)


def report_results(created, skipped, notes):
    """Quiet toast on a clean run; open the full output window only when there is
    something to review (a skipped sheet or a note). Errors raise on their own."""
    if skipped or notes:
        output.print_md("### %s schedules" % NAME_PREFIX.title())
        if created:
            output.print_md("**Created / placed:**")
            for line in created:
                output.print_md("- " + line)
        if skipped:
            output.print_md("**Skipped:**")
            for line in skipped:
                output.print_md("- " + line)
        if notes:
            output.print_md("**Notes:**")
            for line in notes:
                output.print_md("- " + line)
    else:
        try:
            forms.toast("Created / placed on %d sheet(s)." % len(created),
                        title="%s schedules" % NAME_PREFIX.title())
        except Exception:
            output.print_md("%d schedule(s) created / placed." % len(created))


run()

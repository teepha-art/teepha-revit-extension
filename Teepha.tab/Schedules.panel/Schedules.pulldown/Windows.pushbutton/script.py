# -*- coding: utf-8 -*-
"""Windows schedule builder for the Schedules pulldown."""

__title__ = "Windows"
__author__ = "Teepha"
__doc__ = ("Create a Window schedule (Level, Mark, Width, Height, Count), grouped "
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

# --- Category-specific config (Doors button will mirror this block only) --------
CATEGORY = BuiltInCategory.OST_Windows
NAME_PREFIX = "Windows"

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
    """Return the associated Level of the first floor plan placed on the sheet."""
    for vp_id in sheet.GetAllViewports():
        vp = doc.GetElement(vp_id)
        view = doc.GetElement(vp.ViewId)
        if view is not None and view.ViewType == ViewType.FloorPlan:
            gen_level = getattr(view, "GenLevel", None)
            if gen_level:
                return gen_level
    return None


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
    """Create the window schedule filtered to *level*, house-style formatted."""
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
    """Place the schedule near the top-left of the sheet's drawing area."""
    outline = sheet.Outline
    point = XYZ(outline.Min.U + 0.05, outline.Max.V - 0.05, 0.0)
    ScheduleSheetInstance.Create(doc, sheet.Id, schedule.Id, point)


def run():
    sheets = forms.select_sheets(
        title="Select SETTING-OUT sheets for Window schedules",
        button_name="Build window schedules",
        filterfunc=is_setting_out_sheet)
    if not sheets:
        script.exit()

    created, skipped, notes = [], [], []

    t = Transaction(doc, "Windows schedules per sheet")
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

            win_count = len(FilteredElementCollector(doc, schedule.Id)
                            .WhereElementIsNotElementType().ToElementIds())
            tail = "" if win_count else "  (no windows on this level - empty schedule)"
            created.append("%s -> '%s' (%d windows)%s" % (tag, name, win_count, tail))
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    output.print_md("### Window schedules")
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


run()

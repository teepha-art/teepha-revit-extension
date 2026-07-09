# -*- coding: utf-8 -*-
"""Slab (floor) schedule builder for the Schedules pulldown."""

__title__ = "Slabs"
__author__ = "Teepha"
__doc__ = ("Create a Floor (slab) schedule (Type, Default Thickness, Area, Level) "
           "grouped by Type with Area summed per type, sorted by Type, filtered to "
           "each selected sheet's plan level. Prompts for STR / ARC / Both, filtering "
           "by the NEO_STR_ / NEO_ARC_ type-name prefix. Both creates two schedules.")

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewSheet, ViewSchedule, ViewType, BuiltInCategory,
    ElementId, Transaction, ScheduleSortGroupField, ScheduleSortOrder,
    ScheduleFilter, ScheduleFilterType, ScheduleSheetInstance, ScheduleFieldDisplayType,
    FormatOptions, UnitTypeId, XYZ,
)

doc = revit.doc
output = script.get_output()

CATEGORY = BuiltInCategory.OST_Floors

# Slabs sit on C.F.L levels and structural sheets carry C.F.L plans, so we read the
# sheet's plan level as-is (NO F.F.L mapping, same as Columns/Beams).
PLAN_VIEW_TYPES = (ViewType.FloorPlan, ViewType.EngineeringPlan)

# BuiltInParameter ids for the columns, in the exact order requested.
PID_TYPE = -1002050       # Type (instance)
PID_THICKNESS = -1001902  # Default Thickness (type)
PID_AREA = -1012805       # Area (instance)
PID_LEVEL = -1002062      # Level (instance)

# Discipline -> (name prefix in the schedule title, type-name prefix to filter by)
DISCIPLINES = {
    "STR": ("STR SLABS", "NEO_STR_"),
    "ARC": ("ARC SLABS", "NEO_ARC_"),
}


def eid_value(element_id):
    """ElementId's integer value across Revit versions (.Value new, .IntegerValue old)."""
    val = getattr(element_id, "Value", None)
    if val is None:
        val = element_id.IntegerValue
    return int(val)


def get_plan_level_for_sheet(sheet):
    """Return the level of the first plan on the sheet, read as-is (no F.F.L mapping)."""
    for vp_id in sheet.GetAllViewports():
        view = doc.GetElement(doc.GetElement(vp_id).ViewId)
        if view is not None and view.ViewType in PLAN_VIEW_TYPES:
            gen_level = getattr(view, "GenLevel", None)
            if gen_level:
                return gen_level
    return None


def has_plan_level(sheet):
    """Only offer sheets that carry a plan we can resolve a level from."""
    return get_plan_level_for_sheet(sheet) is not None


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


def build_schedule(level, name, type_prefix):
    """Slab schedule (Type, Default Thickness, Area, Level) filtered to *level* and to
    type names starting with *type_prefix*, grouped by Type with Area summed per type."""
    vs = ViewSchedule.CreateSchedule(doc, ElementId(CATEGORY))
    vs.Name = name
    sdef = vs.Definition

    by_pid = {}
    for sf in sdef.GetSchedulableFields():
        by_pid[eid_value(sf.ParameterId)] = sf

    f_type = sdef.AddField(by_pid[PID_TYPE])
    sdef.AddField(by_pid[PID_THICKNESS])
    f_area = sdef.AddField(by_pid[PID_AREA])
    f_level = sdef.AddField(by_pid[PID_LEVEL])

    # Area in m2, 2 decimal places, summed per type row.
    sqm_2dp = FormatOptions(UnitTypeId.SquareMeters)
    sqm_2dp.Accuracy = 0.01
    f_area.SetFormatOptions(sqm_2dp)
    if f_area.CanTotal:
        f_area.DisplayType = ScheduleFieldDisplayType.Totals

    # Filter: this level AND type-name prefix (ARC vs STR).
    sdef.AddFilter(ScheduleFilter(f_level.FieldId, ScheduleFilterType.Equal, level.Id))
    sdef.AddFilter(ScheduleFilter(f_type.FieldId, ScheduleFilterType.BeginsWith, type_prefix))

    # Group + sort by Type, collapse identical types into one row (Area sums).
    sdef.AddSortGroupField(
        ScheduleSortGroupField(f_type.FieldId, ScheduleSortOrder.Ascending))
    sdef.IsItemized = False
    sdef.ShowGrandTotal = False
    return vs


def place_schedule(schedule, sheet):
    """Place the schedule at the top-left, stacked below any schedule already on
    the sheet so multiple schedules don't overlap."""
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
    choice = forms.alert(
        "Which slabs should the schedule show?",
        options=["STR (structural slabs)", "ARC (architectural floors)", "Both"])
    if not choice:
        script.exit()
    if choice.startswith("STR"):
        keys = ["STR"]
    elif choice.startswith("ARC"):
        keys = ["ARC"]
    else:
        keys = ["STR", "ARC"]

    sheets = forms.select_sheets(
        title="Select sheets for Slab schedules",
        button_name="Build slab schedules",
        filterfunc=has_plan_level)
    if not sheets:
        script.exit()

    created, skipped, notes = [], [], []

    t = Transaction(doc, "Slab schedules per sheet")
    t.Start()
    try:
        for sheet in sheets:
            tag = "%s - %s" % (sheet.SheetNumber, sheet.Name)
            level = get_plan_level_for_sheet(sheet)
            if level is None:
                skipped.append("%s -> no plan / no level found" % tag)
                continue

            for key in keys:
                prefix_label, type_prefix = DISCIPLINES[key]
                name = "%s - %s" % (prefix_label, level.Name)
                existing = find_schedule_by_name(name)
                if existing is not None:
                    choice2 = forms.alert(
                        "A schedule named '%s' already exists.\nWhat should I do?" % name,
                        options=["Reuse existing", "Create a new copy", "Skip"])
                    if choice2 == "Skip" or not choice2:
                        skipped.append("%s -> skipped (schedule exists)" % name)
                        continue
                    if choice2 == "Reuse existing":
                        if is_placed_on_sheet(existing.Id, sheet.Id):
                            notes.append("%s -> already had '%s' placed" % (tag, name))
                        else:
                            place_schedule(existing, sheet)
                            created.append("%s -> reused '%s'" % (tag, name))
                        continue
                    suffix = 2
                    while find_schedule_by_name("%s (%d)" % (name, suffix)):
                        suffix += 1
                    name = "%s (%d)" % (name, suffix)

                schedule = build_schedule(level, name, type_prefix)
                place_schedule(schedule, sheet)
                n = len(FilteredElementCollector(doc, schedule.Id)
                        .WhereElementIsNotElementType().ToElementIds())
                tail = "" if n else "  (no %s slabs on this level - empty schedule)" % key
                created.append("%s -> '%s' (%d slabs)%s" % (tag, name, n, tail))
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    report_results(created, skipped, notes)


def report_results(created, skipped, notes):
    """Quiet toast on a clean run; open the full output window only when there is
    something to review (a skipped sheet or a note). Errors raise on their own."""
    if skipped or notes:
        output.print_md("### Slab schedules")
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
                        title="Slab schedules")
        except Exception:
            output.print_md("%d schedule(s) created / placed." % len(created))


run()

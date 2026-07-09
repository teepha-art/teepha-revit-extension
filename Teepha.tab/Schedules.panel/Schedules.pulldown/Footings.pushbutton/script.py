# -*- coding: utf-8 -*-
"""Structural foundation (footing) schedule builder for the Schedules pulldown."""

__title__ = "Footings"
__author__ = "Teepha"
__doc__ = ("Create a Structural Foundation schedule (Mark, Level, Type Mark, Count) "
           "listing ALL footings - pad footings and strip (wall) footings together - "
           "grouped and sorted by Mark with a grand total, and place it on a sheet you "
           "pick. Not level-filtered: strip footings (WallFoundations) carry no level "
           "of their own, so the schedule lists every footing and their Level column "
           "is blank where Revit has no level to report.")

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewSheet, ViewSchedule, ViewType, BuiltInCategory,
    ElementId, Transaction, ScheduleFieldType, ScheduleSortGroupField, ScheduleSortOrder,
    ScheduleSheetInstance, ScheduleFieldDisplayType, XYZ,
)

doc = revit.doc
output = script.get_output()

CATEGORY = BuiltInCategory.OST_StructuralFoundation
SCHEDULE_NAME = "FOOTINGS"

# BuiltInParameter ids for the columns, in the exact order requested.
PID_MARK = -1001203       # Mark (instance)
PID_LEVEL = -1002062      # Level (instance) - blank for wall footings
PID_TYPE_MARK = -1001405  # Type Mark (type)

# Offer sheets that carry a plan (foundation / structural sheets) in the picker.
PLAN_VIEW_TYPES = (ViewType.FloorPlan, ViewType.EngineeringPlan)


def eid_value(element_id):
    """ElementId's integer value across Revit versions (.Value new, .IntegerValue old)."""
    val = getattr(element_id, "Value", None)
    if val is None:
        val = element_id.IntegerValue
    return int(val)


def has_plan(sheet):
    """Only offer sheets that carry a floor/structural plan (e.g. the foundation sheet)."""
    for vp_id in sheet.GetAllViewports():
        view = doc.GetElement(doc.GetElement(vp_id).ViewId)
        if view is not None and view.ViewType in PLAN_VIEW_TYPES:
            return True
    return False


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


def build_schedule(name):
    """Create the footing schedule (Mark, Level, Type Mark, Count) with NO level
    filter - all foundations, grouped and sorted by Mark, grand total on Count."""
    vs = ViewSchedule.CreateSchedule(doc, ElementId(CATEGORY))
    vs.Name = name
    sdef = vs.Definition

    by_pid = {}
    count_field = None
    for sf in sdef.GetSchedulableFields():
        by_pid[eid_value(sf.ParameterId)] = sf
        if sf.FieldType == ScheduleFieldType.Count:
            count_field = sf

    f_mark = sdef.AddField(by_pid[PID_MARK])
    sdef.AddField(by_pid[PID_LEVEL])
    sdef.AddField(by_pid[PID_TYPE_MARK])
    f_count = sdef.AddField(count_field)

    # Group + sort by Mark, collapse identical marks, grand total row.
    sdef.AddSortGroupField(
        ScheduleSortGroupField(f_mark.FieldId, ScheduleSortOrder.Ascending))
    if f_count.CanTotal:
        f_count.DisplayType = ScheduleFieldDisplayType.Totals
    sdef.IsItemized = False
    sdef.ShowGrandTotal = True
    sdef.ShowGrandTotalTitle = True
    sdef.ShowGrandTotalCount = False
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
    sheets = forms.select_sheets(
        title="Select the sheet(s) for the FOOTINGS schedule (e.g. foundation sheet)",
        button_name="Place footing schedule",
        filterfunc=has_plan)
    if not sheets:
        script.exit()

    created, skipped, notes = [], [], []

    t = Transaction(doc, "Footing schedule")
    t.Start()
    try:
        schedule = find_schedule_by_name(SCHEDULE_NAME)
        if schedule is None:
            schedule = build_schedule(SCHEDULE_NAME)
            created.append("created '%s'" % SCHEDULE_NAME)
        else:
            notes.append("reused existing '%s'" % SCHEDULE_NAME)

        for sheet in sheets:
            tag = "%s - %s" % (sheet.SheetNumber, sheet.Name)
            if is_placed_on_sheet(schedule.Id, sheet.Id):
                notes.append("%s -> already had '%s' placed" % (tag, SCHEDULE_NAME))
                continue
            place_schedule(schedule, sheet)
            n = len(FilteredElementCollector(doc, schedule.Id)
                    .WhereElementIsNotElementType().ToElementIds())
            created.append("%s -> placed '%s' (%d footings)" % (tag, SCHEDULE_NAME, n))
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    report_results(created, skipped, notes)


def report_results(created, skipped, notes):
    """Quiet toast on a clean run; open the full output window only when there is
    something to review (a skipped sheet or a note). Errors raise on their own."""
    if skipped or notes:
        output.print_md("### Footing schedule")
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
            forms.toast("Footing schedule placed on %d sheet(s)." % len(created),
                        title="Footings")
        except Exception:
            output.print_md("Footing schedule created / placed.")


run()

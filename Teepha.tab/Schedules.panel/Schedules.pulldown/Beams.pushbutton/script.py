# -*- coding: utf-8 -*-
"""Structural beam schedule builder for the Schedules pulldown."""

__title__ = "Beams"
__author__ = "Teepha"
__doc__ = ("Create a Structural Framing (beam) schedule (Mark, Type Mark, Reference "
           "Level, Count), grouped and sorted by Mark, filtered to each selected "
           "sheet's plan level, and place it on that sheet.")

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewSheet, ViewSchedule, ViewType, BuiltInCategory,
    ElementId, Transaction, ScheduleFieldType, ScheduleSortGroupField, ScheduleSortOrder,
    ScheduleFilter, ScheduleFilterType, ScheduleSheetInstance, ScheduleFieldDisplayType,
    XYZ,
)

doc = revit.doc
output = script.get_output()

# --- Category-specific config -----------------------------------------------------
CATEGORY = BuiltInCategory.OST_StructuralFraming
NAME_PREFIX = "BEAMS"

# Beams sit on C.F.L levels and structural sheets carry C.F.L plans, so we read the
# sheet's plan level as-is (NO F.F.L mapping, same as Columns).
PLAN_VIEW_TYPES = (ViewType.FloorPlan, ViewType.EngineeringPlan)

# BuiltInParameter ids for the columns, in the exact order requested.
PID_MARK = -1001203       # Mark (instance)
PID_TYPE_MARK = -1001405  # Type Mark (type)
PID_REF_LEVEL = -1001383  # Reference Level (instance)


def eid_value(element_id):
    """ElementId's integer value across Revit versions (.Value new, .IntegerValue old)."""
    val = getattr(element_id, "Value", None)
    if val is None:
        val = element_id.IntegerValue
    return int(val)


def get_plan_level_for_sheet(sheet):
    """Return the level of the first plan on the sheet, read as-is (no F.F.L mapping).

    Accepts floor plans and structural (engineering) plans."""
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


def build_schedule(level, name):
    """Create the beam schedule (Mark, Type Mark, Reference Level, Count) filtered to
    *level* (Reference Level), grouped and sorted by Mark, with a grand total that
    sums the Count column."""
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
    sdef.AddField(by_pid[PID_TYPE_MARK])
    f_ref = sdef.AddField(by_pid[PID_REF_LEVEL])
    f_count = sdef.AddField(count_field)

    # Filter to this reference level only.
    sdef.AddFilter(ScheduleFilter(f_ref.FieldId, ScheduleFilterType.Equal, level.Id))

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
        title="Select sheets for Beam schedules",
        button_name="Build beam schedules",
        filterfunc=has_plan_level)
    if not sheets:
        script.exit()

    created, skipped, notes = [], [], []

    t = Transaction(doc, "Beam schedules per sheet")
    t.Start()
    try:
        for sheet in sheets:
            tag = "%s - %s" % (sheet.SheetNumber, sheet.Name)
            level = get_plan_level_for_sheet(sheet)
            if level is None:
                skipped.append("%s -> no plan / no level found" % tag)
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

            beam_count = len(FilteredElementCollector(doc, schedule.Id)
                             .WhereElementIsNotElementType().ToElementIds())
            tail = "" if beam_count else "  (no beams on this level - empty schedule)"
            created.append("%s -> '%s' (%d beams)%s" % (tag, name, beam_count, tail))
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

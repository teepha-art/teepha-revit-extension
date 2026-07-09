# -*- coding: utf-8 -*-
"""Sheet list builder for the Schedules pulldown."""

__title__ = "Sheet List"
__author__ = "Teepha"
__doc__ = ("Create a Sheet List (Sheet Number, Sheet Name, Sheet Group) sorted by "
           "Sheet Group then Sheet Number, and place it on a sheet you pick (e.g. "
           "the drawing list / cover sheet). Falls back to Sheet Number / Sheet "
           "Name only, sorted by Sheet Number, if this project has no Sheet Group "
           "parameter. Respects 'Appears In Sheet List'.")

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewSchedule, ViewType, Transaction,
    ScheduleSortGroupField, ScheduleSortOrder, ScheduleSheetInstance, XYZ,
)

doc = revit.doc
output = script.get_output()

SCHEDULE_NAME = "SHEET LIST"

# BuiltInParameter ids: stable across every project, so safe to hardcode.
PID_SHEET_NUMBER = -1007401  # Sheet Number
PID_SHEET_NAME = -1007400    # Sheet Name

# "Sheet Group" is a project parameter - its ElementId is assigned per-document,
# so it can't be hardcoded like the BuiltInParameters above. Looked up by name
# at runtime instead (see find_sheet_group_field).
SHEET_GROUP_NAME = "Sheet Group"

# The target is a cover / index / drawing-list sheet: offer only sheets that carry
# no plan view (floor plans or structural plans).
PLAN_VIEW_TYPES = (ViewType.FloorPlan, ViewType.EngineeringPlan)


def eid_value(element_id):
    """ElementId's integer value across Revit versions (.Value new, .IntegerValue old)."""
    val = getattr(element_id, "Value", None)
    if val is None:
        val = element_id.IntegerValue
    return int(val)


def has_no_plan(sheet):
    """True when the sheet carries no floor/structural plan (cover pages, indexes)."""
    for vp_id in sheet.GetAllViewports():
        view = doc.GetElement(doc.GetElement(vp_id).ViewId)
        if view is not None and view.ViewType in PLAN_VIEW_TYPES:
            return False
    return True


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


def find_sheet_group_field(sdef):
    """Look up the 'Sheet Group' schedulable field by name rather than by
    ElementId, since project parameter ids aren't stable across documents.
    Returns None if this project has no such parameter on sheets."""
    for sf in sdef.GetSchedulableFields():
        if sf.GetName(doc).strip().lower() == SHEET_GROUP_NAME.lower():
            return sf
    return None


def build_sheet_list(name):
    """Create the sheet list (Sheet Number, Sheet Name, plus Sheet Group when the
    project has that parameter), itemized, sorted by Sheet Group then Sheet
    Number (or just Sheet Number if there's no Sheet Group parameter here).
    Revit natively excludes sheets whose 'Appears In Sheet List' is unticked.

    Returns (schedule, has_group) - has_group is False when the Sheet Group
    column/sort had to be skipped."""
    vs = ViewSchedule.CreateSheetList(doc)
    vs.Name = name
    sdef = vs.Definition

    by_pid = {}
    for sf in sdef.GetSchedulableFields():
        by_pid[eid_value(sf.ParameterId)] = sf

    f_num = sdef.AddField(by_pid[PID_SHEET_NUMBER])
    sdef.AddField(by_pid[PID_SHEET_NAME])

    group_field = find_sheet_group_field(sdef)
    has_group = group_field is not None
    if has_group:
        f_group = sdef.AddField(group_field)
        # Sort: Sheet Group first (groups cluster), then Sheet Number. Group
        # stays a plain repeated column - no header sections.
        sdef.AddSortGroupField(
            ScheduleSortGroupField(f_group.FieldId, ScheduleSortOrder.Ascending))
    sdef.AddSortGroupField(
        ScheduleSortGroupField(f_num.FieldId, ScheduleSortOrder.Ascending))
    sdef.IsItemized = True
    sdef.ShowGrandTotal = False
    return vs, has_group


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
        title="Select the target sheet for the SHEET LIST (e.g. drawing list)",
        button_name="Place sheet list",
        filterfunc=has_no_plan)
    if not sheets:
        script.exit()

    created, skipped, notes = [], [], []

    t = Transaction(doc, "Sheet list")
    t.Start()
    try:
        schedule = find_schedule_by_name(SCHEDULE_NAME)
        if schedule is None:
            schedule, has_group = build_sheet_list(SCHEDULE_NAME)
            created.append("created '%s'" % SCHEDULE_NAME)
            if not has_group:
                notes.append(
                    "No '%s' parameter found on sheets in this project - schedule "
                    "created with Sheet Number / Sheet Name only, sorted by Sheet "
                    "Number." % SHEET_GROUP_NAME)
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
            created.append("%s -> placed '%s' (%d sheets listed)" % (tag, SCHEDULE_NAME, n))
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    report_results(created, skipped, notes)


def report_results(created, skipped, notes):
    """Quiet toast on a clean run; open the full output window only when there is
    something to review (a skipped sheet or a note). Errors raise on their own."""
    if skipped or notes:
        output.print_md("### Sheet list")
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
            forms.toast("Sheet list placed on %d sheet(s)." % len(created),
                        title="Sheet list")
        except Exception:
            output.print_md("Sheet list created / placed.")


run()

# -*- coding: utf-8 -*-
"""Sheet-set generator for the Sheets pulldown."""

import re
from collections import Counter, defaultdict

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewSheet, FamilySymbol, BuiltInCategory,
    BuiltInParameter, Transaction, ElementId,
)
from Autodesk.Revit.DB.ExtensibleStorage import (
    Schema, SchemaBuilder, AccessLevel, Entity, DataStorage,
)
from System import Guid
from System.Collections.Generic import List, IList
from System.Windows import Thickness, GridLength, TextWrapping, FontStyles
from System.Windows.Controls import (
    Expander, StackPanel, Grid, ColumnDefinition, RowDefinition, TextBlock,
    TextBox, ComboBox, ComboBoxItem, Orientation, DockPanel, Button, Dock,
)

__title__ = "Create Group"
__author__ = "Teepha"
__doc__ = ("Generate a batch of sheets for one or more disciplines in one pass: "
           "pick a Sheet Group, a sample sheet-number pattern, a count, and a "
           "title block, then review the full list before anything is created. "
           "Re-run on a group with a stored pattern to edit it and renumber the "
           "sheets it previously generated.")

doc = revit.doc
output = script.get_output()

SHEET_GROUP_PARAM = "Sheet Group"

# Standard five disciplines: (display label, default Sheet Group text). Matches
# the project's existing Sheet Group values so the pre-fill is correct out of
# the box; the text field stays fully editable for a different naming scheme.
STANDARD_DISCIPLINES = [
    ("Architectural", "01-ARCHITECTURAL DRAWING AND DETAILS"),
    ("Structural", "02-STRUCTURAL DRAWINGS AND DETAILS"),
    ("Sewage & Drainage", "03-MECHANICAL - SEWAGE AND DRAINAGE"),
    ("Water Supply", "04-MECHANICAL - WATER SUPPLY"),
    ("Electrical", "05-MECHANICAL - ELECTRICAL"),
]

# Same trailing-counter parsing convention as CORRECT: prefix + trailing digits.
NUM_RE = re.compile(r"^(.*?)(\d+)$")


# --- Pattern memory (Extensible Storage) ------------------------------------
# One DataStorage element per Sheet Group that has ever been generated through
# this tool, carrying: the group name (lookup key), the prefix/width the group
# was last generated/edited with, and the ElementIds of every sheet this tool
# created for that group. Only sheets tracked here are ever touched by a
# pattern re-edit - sheets that happen to share a Sheet Group value but were
# created some other way are never swept up.
PATTERN_SCHEMA_GUID = Guid("8f3a1c62-0b7a-4b6a-9e2d-6a1f9c8b3d47")
F_GROUP = "GroupName"
F_PREFIX = "Prefix"
F_WIDTH = "Width"
F_SHEETIDS = "SheetIds"

_schema_cache = [None]


def get_pattern_schema():
    if _schema_cache[0] is not None:
        return _schema_cache[0]
    schema = Schema.Lookup(PATTERN_SCHEMA_GUID)
    if schema is None:
        sb = SchemaBuilder(PATTERN_SCHEMA_GUID)
        sb.SetSchemaName("TeephaCreateGroupPattern")
        sb.SetReadAccessLevel(AccessLevel.Public)
        sb.SetWriteAccessLevel(AccessLevel.Public)
        sb.SetVendorId("TEEPHA")
        sb.AddSimpleField(F_GROUP, str)
        sb.AddSimpleField(F_PREFIX, str)
        sb.AddSimpleField(F_WIDTH, int)
        sb.AddArrayField(F_SHEETIDS, ElementId)
        schema = sb.Finish()
    _schema_cache[0] = schema
    return schema


def find_data_storage(group):
    schema = get_pattern_schema()
    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        ent = ds.GetEntity(schema)
        if ent.IsValid() and ent.Get[str](F_GROUP) == group:
            return ds, ent
    return None, None


def load_pattern_record(group):
    """{'prefix', 'width', 'sheet_ids'} for this Sheet Group, or None if this
    tool has never generated sheets for it."""
    if not group:
        return None
    ds, ent = find_data_storage(group)
    if ds is None:
        return None
    prefix = ent.Get[str](F_PREFIX)
    width = ent.Get[int](F_WIDTH)
    ids = list(ent.Get[IList[ElementId]](F_SHEETIDS))
    return {"prefix": prefix, "width": width, "sheet_ids": ids}


def save_pattern_record(group, prefix, width, sheet_ids):
    """Must be called inside an open Transaction."""
    schema = get_pattern_schema()
    ds, _ = find_data_storage(group)
    if ds is None:
        ds = DataStorage.Create(doc)
    entity = Entity(schema)
    entity.Set[str](F_GROUP, group)
    entity.Set[str](F_PREFIX, prefix)
    entity.Set[int](F_WIDTH, width)
    entity.Set[IList[ElementId]](F_SHEETIDS, List[ElementId](sheet_ids))
    ds.SetEntity(entity)


def reconstruct_sample(record):
    """Sample sheet-number text (e.g. 'COR_P1_V2_AR-00') representing a stored
    record's current lowest tracked sheet number, for pre-filling the pattern
    field. '' if there is nothing (yet) to show."""
    if not record:
        return ""
    vals = []
    for eid in record["sheet_ids"]:
        s = doc.GetElement(eid)
        if s is None:
            continue
        m = NUM_RE.match(s.SheetNumber or "")
        if m:
            vals.append(int(m.group(2)))
    if not vals:
        return ""
    return "%s%s" % (record["prefix"], str(min(vals)).zfill(record["width"]))


def eid_value(element_id):
    val = getattr(element_id, "Value", None)
    if val is None:
        val = element_id.IntegerValue
    return int(val)


def sheet_group(sheet):
    p = sheet.LookupParameter(SHEET_GROUP_PARAM)
    if p and p.HasValue:
        v = p.AsString()
        return v.strip() if v else ""
    return ""


def get_titleblock_types():
    """[(display_name, ElementId), ...] sorted by display name."""
    types = []
    for t in (FilteredElementCollector(doc).OfClass(FamilySymbol)
              .OfCategory(BuiltInCategory.OST_TitleBlocks)):
        fam = t.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME).AsString()
        name = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
        types.append(("%s : %s" % (fam, name), t.Id))
    return sorted(types, key=lambda x: x[0])


def compute_default_titleblocks(all_sheets, titleblock_types):
    """Per Sheet Group, the most-common title block already used there (house
    style varies by group - e.g. Commercial genuinely uses a different block).
    Falls back to the project's overall most-common title block for a group
    that doesn't exist yet."""
    tb_by_sheet = {}
    for tb in (FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks)
               .WhereElementIsNotElementType().ToElements()):
        tb_by_sheet[eid_value(tb.OwnerViewId)] = tb

    per_group = defaultdict(Counter)
    overall = Counter()
    for s in all_sheets:
        tb = tb_by_sheet.get(eid_value(s.Id))
        if not tb:
            continue
        tb_id = eid_value(tb.GetTypeId())
        g = sheet_group(s)
        if g:
            per_group[g][tb_id] += 1
        overall[tb_id] += 1

    overall_default = overall.most_common(1)[0][0] if overall else None
    defaults = {}
    for g, counter in per_group.items():
        defaults[g] = counter.most_common(1)[0][0]
    return defaults, overall_default


def parse_pattern(pattern):
    """(prefix, start_int, width) or None if the pattern has no trailing counter."""
    m = NUM_RE.match(pattern or "")
    if not m:
        return None
    prefix, digits = m.group(1), m.group(2)
    return prefix, int(digits), len(digits)


class Section(object):
    """One discipline block: its WPF controls plus a resolved default title block."""

    def __init__(self, panel, label, default_group, titleblock_items, default_tb_id,
                 removable=False, on_remove=None):
        self.titleblock_items = titleblock_items
        self.panel = panel
        self.on_remove = on_remove
        self.label = label

        self.expander = Expander()
        if removable:
            header = DockPanel()
            remove_btn = Button(Content="×")  # "x"
            remove_btn.Width = 22
            remove_btn.Height = 20
            remove_btn.Padding = Thickness(0)
            remove_btn.Margin = Thickness(10, 0, 0, 0)
            remove_btn.ToolTip = "Remove this discipline"
            remove_btn.Click += self._remove_clicked
            DockPanel.SetDock(remove_btn, Dock.Right)
            header.Children.Add(remove_btn)
            lbl = TextBlock(Text=label)
            lbl.VerticalAlignment = remove_btn.VerticalAlignment
            header.Children.Add(lbl)
            self.expander.Header = header
        else:
            self.expander.Header = label
        self.expander.IsExpanded = False
        self.expander.Margin = Thickness(0, 0, 0, 8)
        self.expander.Padding = Thickness(4)

        body = Grid()
        for _ in range(5):
            body.RowDefinitions.Add(RowDefinition(Height=GridLength.Auto))
        col_label = ColumnDefinition(Width=GridLength(130))
        col_input = ColumnDefinition()
        body.ColumnDefinitions.Add(col_label)
        body.ColumnDefinitions.Add(col_input)

        def add_row(row, text, control, label_col=0):
            lbl = TextBlock(Text=text)
            lbl.Margin = Thickness(0, 4, 8, 4)
            Grid.SetRow(lbl, row)
            Grid.SetColumn(lbl, label_col)
            body.Children.Add(lbl)
            control.Margin = Thickness(0, 4, 0, 4)
            Grid.SetRow(control, row)
            Grid.SetColumn(control, 1)
            body.Children.Add(control)

        self.group_tb = TextBox(Text=default_group)
        add_row(0, "Sheet Group", self.group_tb)

        record = load_pattern_record(default_group) if default_group else None
        self.pattern_tb = TextBox(Text=reconstruct_sample(record))
        self.pattern_tb.ToolTip = 'Sample number, e.g. "COR_P1_V2_AR-00"'
        add_row(1, "Sheet Number pattern", self.pattern_tb)

        self.count_tb = TextBox()
        add_row(2, "Sheet Count", self.count_tb)

        self.tb_combo = ComboBox()
        selected_index = 0
        for i, (name, eid) in enumerate(titleblock_items):
            item = ComboBoxItem(Content=name)
            self.tb_combo.Items.Add(item)
            if default_tb_id is not None and eid_value(eid) == default_tb_id:
                selected_index = i
        if titleblock_items:
            self.tb_combo.SelectedIndex = selected_index
        add_row(3, "Title Block", self.tb_combo)

        note = TextBlock()
        note.TextWrapping = TextWrapping.Wrap
        note.FontStyle = FontStyles.Italic
        note.Opacity = 0.75
        if record and record["sheet_ids"]:
            n_tracked = len([i for i in record["sheet_ids"] if doc.GetElement(i)])
            note.Text = ("%d sheet(s) already generated with this pattern. Change "
                         "the number above to renumber them; add a Count to also "
                         "create more." % n_tracked)
        Grid.SetRow(note, 4)
        Grid.SetColumn(note, 1)
        body.Children.Add(note)

        self.expander.Content = body
        panel.Children.Add(self.expander)

    def _remove_clicked(self, sender, args):
        self.panel.Children.Remove(self.expander)
        if self.on_remove:
            self.on_remove(self)

    def read(self):
        """Validated dict, an error string, or None if out of scope (collapsed,
        blank, or an unchanged stored pattern with nothing new to add)."""
        if not self.expander.IsExpanded:
            return None
        group = (self.group_tb.Text or "").strip()
        pattern = (self.pattern_tb.Text or "").strip()
        count_raw = (self.count_tb.Text or "").strip()
        if not group and not pattern and not count_raw:
            return None  # expanded but left blank - skip quietly

        label = self.label
        if not group:
            return "[%s] Sheet Group is required." % label
        parsed = parse_pattern(pattern)
        if not parsed:
            return ("[%s] Sheet Number pattern must end in digits, "
                     "e.g. 'COR_P1_V2_AR-00'." % label)
        prefix, start, width = parsed

        count = 0
        if count_raw:
            if not count_raw.isdigit():
                return "[%s] Sheet Count must be a whole number (0 or more)." % label
            count = int(count_raw)

        if self.tb_combo.SelectedIndex < 0:
            return "[%s] Pick a Title Block." % label
        tb_id = self.titleblock_items[self.tb_combo.SelectedIndex][1]

        existing_record = load_pattern_record(group)
        is_edit = False
        if existing_record:
            current_sample = reconstruct_sample(existing_record)
            current_parsed = parse_pattern(current_sample) if current_sample else None
            if current_parsed:
                cur_prefix, cur_start, cur_width = current_parsed
                is_edit = (prefix != cur_prefix or width != cur_width or start != cur_start)
            else:
                # No live tracked sheets left to compare against (all deleted) -
                # nothing to renumber either way, so this can't be a renumber edit.
                is_edit = False

        if not existing_record and count < 1:
            return ("[%s] Sheet Count must be a positive whole number for a "
                     "new group." % label)
        if existing_record and not is_edit and count == 0:
            return None  # unchanged pattern, nothing new requested - skip quietly

        return {
            "group": group, "prefix": prefix, "start": start, "width": width,
            "count": count, "tb_id": tb_id, "label": label,
            "existing_record": existing_record, "is_edit": is_edit,
        }


class CreateGroupWindow(forms.WPFWindow):
    def __init__(self, xaml_file, all_sheets, titleblock_items, group_defaults,
                 overall_default):
        forms.WPFWindow.__init__(self, xaml_file)
        self.all_sheets = all_sheets
        self.titleblock_items = titleblock_items
        self.group_defaults = group_defaults
        self.overall_default = overall_default
        self.sections = []
        self.result = None

        for label, default_group in STANDARD_DISCIPLINES:
            default_tb = group_defaults.get(default_group, overall_default)
            self.sections.append(
                Section(self.SectionsPanel, label, default_group,
                        titleblock_items, default_tb))

    def add_discipline(self, sender, args):
        self.sections.append(
            Section(self.SectionsPanel, "New Discipline", "",
                    self.titleblock_items, self.overall_default,
                    removable=True, on_remove=self._remove_section))

    def _remove_section(self, section):
        self.sections.remove(section)

    def cancel_clicked(self, sender, args):
        self.result = None
        self.Close()

    def create_sheets(self, sender, args):
        errors = []
        rows = []
        for sec in self.sections:
            r = sec.read()
            if r is None:
                continue
            if isinstance(r, str):
                errors.append(r)
            else:
                rows.append(r)

        if errors:
            forms.alert("Fix the following before continuing:\n\n" + "\n".join(errors))
            return
        if not rows:
            forms.alert("No sections filled in - expand at least one discipline "
                        "and fill in its fields.")
            return

        self.result = rows
        self.Close()


def compute_operations(rows):
    """rows -> per-section ops: {group, label, tb_id, prefix, final_width,
    renumbers: [(sheet, old_number, new_number), ...], new_numbers: [str, ...]}.

    Three cases per section, matched on whether a stored pattern exists and
    whether the typed pattern differs from it (edit):
      - new group (no stored record): plain sequential create, as core stage.
      - unchanged pattern: existing tracked sheets keep their numbers; any
        requested extra Count continues right after the highest tracked index.
      - edited pattern: every tracked sheet is renumbered to the new
        prefix/width/start, and any requested extra Count continues right
        after the renumbered set. Padding widens (never narrows) to fit the
        combined old+new count, same convention as CORRECT.
    """
    ops = []
    for row in rows:
        group, prefix, start = row["group"], row["prefix"], row["start"]
        width, count, tb_id, label = row["width"], row["count"], row["tb_id"], row["label"]
        existing_record, is_edit = row["existing_record"], row["is_edit"]

        ordered_old = []
        if existing_record:
            old_ids = [i for i in existing_record["sheet_ids"] if doc.GetElement(i) is not None]
            ordered_old = sorted((doc.GetElement(i) for i in old_ids),
                                 key=lambda s: s.SheetNumber)
        n_old = len(ordered_old)

        renumbers = []
        if is_edit:
            final_total = n_old + count
            tw = max(width, len(str(start + final_total - 1))) if final_total > 0 else width
            for i, s in enumerate(ordered_old):
                new_num = "%s%s" % (prefix, str(start + i).zfill(tw))
                renumbers.append((s, s.SheetNumber, new_num))
            new_numbers = ["%s%s" % (prefix, str(start + n_old + i).zfill(tw))
                          for i in range(count)]
        elif existing_record:
            idxs = []
            for s in ordered_old:
                m = NUM_RE.match(s.SheetNumber or "")
                if m:
                    idxs.append(int(m.group(2)))
            base_next = (max(idxs) + 1) if idxs else start
            final_hi = base_next + count - 1 if count > 0 else base_next - 1
            tw = max(width, len(str(final_hi))) if count > 0 else width
            new_numbers = ["%s%s" % (prefix, str(base_next + i).zfill(tw))
                          for i in range(count)]
        else:
            final_hi = start + count - 1
            tw = max(width, len(str(final_hi)))
            new_numbers = ["%s%s" % (prefix, str(start + i).zfill(tw)) for i in range(count)]

        ops.append({
            "group": group, "label": label, "tb_id": tb_id,
            "prefix": prefix, "final_width": tw,
            "renumbers": renumbers, "new_numbers": new_numbers,
        })
    return ops


def check_collisions(ops):
    """No duplicate within the new set, and no collision with existing sheets
    not being vacated by this same batch. Returns conflicting numbers (empty
    = clean)."""
    existing = set(s.SheetNumber for s in FilteredElementCollector(doc).OfClass(ViewSheet))
    vacated = set(old for op in ops for (_, old, _) in op["renumbers"])
    baseline = existing - vacated

    seen = set()
    conflicts = []
    for op in ops:
        for (_, _, new) in op["renumbers"]:
            if new in seen or new in baseline:
                conflicts.append(new)
            seen.add(new)
        for new in op["new_numbers"]:
            if new in seen or new in baseline:
                conflicts.append(new)
            seen.add(new)
    return conflicts


def build_preview_lines(ops):
    lines = []
    total_new = total_renum = 0
    for op in ops:
        tb_sym = doc.GetElement(op["tb_id"])
        tb_name = tb_sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
        lines.append("%s  ->  %s   (title block: %s)" % (op["label"], op["group"], tb_name))
        if op["renumbers"]:
            lines.append("  Renumbering %d existing sheet(s):" % len(op["renumbers"]))
            for (_, old, new) in op["renumbers"]:
                lines.append("    %s  ->  %s" % (old, new))
                total_renum += 1
        if op["new_numbers"]:
            lines.append("  Creating %d new sheet(s):" % len(op["new_numbers"]))
            for n in op["new_numbers"]:
                lines.append("    %s" % n)
                total_new += 1
        lines.append("")
    return lines, total_new, total_renum


def print_preview_output(lines, total_new, total_renum, section_count):
    output.print_md("# CREATE GROUP - preview")
    for line in lines:
        output.print_md(line if line.strip() else "&nbsp;")
    output.print_md("---")
    output.print_md("**Total: %d new, %d renumbered, across %d section(s).**"
                    % (total_new, total_renum, section_count))


def apply_all(ops):
    t = Transaction(doc, "CREATE GROUP - batch generate/renumber")
    t.Start()
    try:
        # Phase 1: collision-safe two-phase renumber, combined across all
        # sections so no cross-section collision is possible mid-transaction.
        all_renumbers = [(s, old, new) for op in ops for (s, old, new) in op["renumbers"]]
        existing_all = set(s.SheetNumber for s in FilteredElementCollector(doc).OfClass(ViewSheet))
        temps, i = {}, 0
        for (s, old, new) in all_renumbers:
            while ("ZZZ-CREATEGROUP-TMP-%d" % i) in existing_all:
                i += 1
            temp = "ZZZ-CREATEGROUP-TMP-%d" % i
            temps[eid_value(s.Id)] = temp
            existing_all.add(temp)
            i += 1
        for (s, old, new) in all_renumbers:
            s.SheetNumber = temps[eid_value(s.Id)]
        for (s, old, new) in all_renumbers:
            s.SheetNumber = new

        # Phase 2: create new sheets.
        for op in ops:
            op["created_sheets"] = []
            for n in op["new_numbers"]:
                sheet = ViewSheet.Create(doc, op["tb_id"])
                sheet.SheetNumber = n
                p = sheet.LookupParameter(SHEET_GROUP_PARAM)
                if p and not p.IsReadOnly:
                    p.Set(op["group"])
                op["created_sheets"].append(sheet)

        # Phase 3: persist/refresh the pattern record for every touched group.
        for op in ops:
            final_ids = [s.Id for (s, _, _) in op["renumbers"]] + \
                        [s.Id for s in op["created_sheets"]]
            save_pattern_record(op["group"], op["prefix"], op["final_width"], final_ids)

        t.Commit()
    except Exception:
        t.RollBack()
        raise


def toast(msg):
    try:
        forms.toast(msg, title="CREATE GROUP")
    except Exception:
        output.print_md(msg)


# Confirmation dialog with the full preview embedded in a scrollable list, so
# approval never depends on the separate output window being readable (the
# WebBrowser-based output window can fail to paint before a modal dialog
# blocks the UI thread).
CONFIRM_XAML = (
    '<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
    'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" '
    'ShowInTaskbar="False" Width="560" Height="560" MinWidth="460" MinHeight="360" '
    'ResizeMode="CanResizeWithGrip" WindowStartupLocation="CenterScreen" '
    'Title="CREATE GROUP - confirm generation">'
    '<DockPanel Margin="12">'
    '<TextBlock x:Name="header_tb" DockPanel.Dock="Top" TextWrapping="Wrap" '
    'Margin="0,0,0,8"/>'
    '<StackPanel DockPanel.Dock="Bottom" Orientation="Horizontal" '
    'HorizontalAlignment="Right" Margin="0,10,0,0">'
    '<Button x:Name="cancel_btn" Content="Cancel" Width="90" Height="28" '
    'Margin="0,0,10,0" Click="cancel_clicked"/>'
    '<Button x:Name="ok_btn" Content="Apply" Width="130" Height="28" '
    'Click="ok_clicked"/>'
    '</StackPanel>'
    '<TextBox x:Name="list_tb" IsReadOnly="True" FontFamily="Consolas" '
    'FontSize="13" VerticalScrollBarVisibility="Auto" '
    'HorizontalScrollBarVisibility="Auto" TextWrapping="NoWrap"/>'
    '</DockPanel></Window>'
)


class ConfirmWindow(forms.WPFWindow):
    """Modal confirm dialog carrying its own scrollable preview list."""

    def __init__(self, header, lines):
        forms.WPFWindow.__init__(self, CONFIRM_XAML, literal_string=True)
        self.header_tb.Text = header
        self.list_tb.Text = "\r\n".join(lines)
        self.confirmed = False

    def ok_clicked(self, sender, args):
        self.confirmed = True
        self.Close()

    def cancel_clicked(self, sender, args):
        self.Close()


def run():
    all_sheets = [s for s in FilteredElementCollector(doc).OfClass(ViewSheet)
                  if not s.IsPlaceholder]
    titleblock_items = get_titleblock_types()
    if not titleblock_items:
        forms.alert("No Title Block types are loaded in this project.", exitscript=True)

    group_defaults, overall_default = compute_default_titleblocks(
        all_sheets, titleblock_items)

    win = CreateGroupWindow("ui.xaml", all_sheets, titleblock_items,
                            group_defaults, overall_default)
    win.ShowDialog()

    if not win.result:
        script.exit()

    ops = compute_operations(win.result)

    conflicts = check_collisions(ops)
    if conflicts:
        forms.alert(
            "Aborted - the following sheet number(s) would collide with an "
            "existing sheet or duplicate within this batch. No changes were "
            "made.\n\n" + "\n".join(sorted(set(conflicts))),
            title="CREATE GROUP - collision detected", exitscript=True)

    lines, total_new, total_renum = build_preview_lines(ops)
    print_preview_output(lines, total_new, total_renum, len(ops))

    header = ("Found %d sheet(s) to create and %d to renumber, across %d "
              "section(s). Review the full list below, then apply or cancel."
              % (total_new, total_renum, len(ops)))
    confirm = ConfirmWindow(header, lines)
    confirm.ShowDialog()
    if not confirm.confirmed:
        output.print_md("---")
        output.print_md("**Result: cancelled by user. Nothing changed.**")
        toast("Cancelled - no changes made.")
        script.exit()

    apply_all(ops)

    output.print_md("---")
    output.print_md("**Result: applied - %d sheet(s) created, %d renumbered, "
                    "across %d section(s).**" % (total_new, total_renum, len(ops)))
    toast("Created %d, renumbered %d sheet(s)." % (total_new, total_renum))


run()

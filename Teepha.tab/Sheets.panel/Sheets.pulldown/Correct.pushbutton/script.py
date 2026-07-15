# -*- coding: utf-8 -*-
"""Sheet number corrector for the Sheets pulldown."""

__title__ = "CORRECT"
__author__ = "Teepha"
__doc__ = ("Audit and fix sheet numbering within a Sheet Group (or all groups): "
           "close gaps, normalise padding (e.g. AR-002 -> AR-02) and resolve "
           "duplicate numbers. Shows the full BEFORE -> AFTER mapping for approval, "
           "applies collision-safely, and never changes Sheet Names - only numbers.")

import re
from collections import Counter, defaultdict

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import FilteredElementCollector, ViewSheet, Transaction

doc = revit.doc
output = script.get_output()

# "Sheet Group" is a project parameter (id assigned per-document), so it is looked
# up by name at runtime rather than hardcoded - same approach as the Sheet List button.
SHEET_GROUP_PARAM = "Sheet Group"
ALL_GROUPS = "<All groups>"

# A sheet number is split into a leading prefix and a TRAILING run of digits, which
# is the counter. "COR_P1_V2_AR-00" -> ("COR_P1_V2_AR-", "00"). Sheets with no
# trailing-digit counter don't match and are ignored by the audit.
NUM_RE = re.compile(r"^(.*?)(\d+)$")


def eid_value(element_id):
    """ElementId's integer value across Revit versions (.Value new, .IntegerValue old)."""
    val = getattr(element_id, "Value", None)
    if val is None:
        val = element_id.IntegerValue
    return int(val)


def get_sheets():
    """All real sheets in the project (placeholder sheets excluded)."""
    return [s for s in FilteredElementCollector(doc).OfClass(ViewSheet)
            if not s.IsPlaceholder]


def sheet_group(sheet):
    """Value of the 'Sheet Group' project parameter, or '' when unset/absent."""
    p = sheet.LookupParameter(SHEET_GROUP_PARAM)
    if p and p.HasValue:
        v = p.AsString()
        return v.strip() if v else ""
    return ""


def parse_number(number):
    """(prefix, digits) for a sheet number, or None when there is no trailing counter."""
    m = NUM_RE.match(number or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def analyze_prefix(group_name, prefix, entries):
    """Compute the canonical (contiguous, consistently-padded) sequence for one
    prefix-subgroup and the change list to reach it.

    entries: list of (sheet, value_int, width). The AFTER sequence keeps the group's
    existing start value (its minimum counter), reassigns contiguous values in the
    order sheets currently sort by, and pads to the group's dominant width (widened
    only if the highest index needs more digits). Sheets whose number is already
    correct produce no change, so a clean group is a no-op."""
    widths = [w for (_, _, w) in entries]
    width_counts = Counter(widths)
    top = max(width_counts.values())
    target_width = min(w for w, c in width_counts.items() if c == top)  # mode, tie->smaller

    values = [v for (_, v, _) in entries]
    value_counts = Counter(values)
    duplicates = sorted(v for v, c in value_counts.items() if c > 1)
    lo, hi = min(values), max(values)
    present = set(values)
    gaps = [v for v in range(lo, hi + 1) if v not in present]
    malformed = [s for (s, v, w) in entries if w != target_width]

    # Canonical order: by current value, then name, then id (stable for duplicates).
    ordered = sorted(entries, key=lambda e: (e[1], e[0].Name or "", eid_value(e[0].Id)))
    final_hi = lo + len(ordered) - 1
    tw = max(target_width, len(str(final_hi)))

    changes = []
    for i, (s, v, w) in enumerate(ordered):
        new_num = "%s%s" % (prefix, str(lo + i).zfill(tw))
        if new_num != s.SheetNumber:
            changes.append((s, s.SheetNumber, new_num))

    return {"group": group_name, "prefix": prefix, "count": len(entries),
            "target_width": tw, "duplicates": duplicates, "gaps": gaps,
            "malformed": malformed, "changes": changes}


def build_group_results(group_name, sheets_in_group):
    """Split a group's sheets by prefix and analyze each prefix-subgroup.
    Returns (results, ignored) - ignored are sheets with no number pattern."""
    parsed, ignored = [], []
    for s in sheets_in_group:
        p = parse_number(s.SheetNumber)
        (parsed if p else ignored).append((s, p) if p else s)

    by_prefix = defaultdict(list)
    for s, (prefix, digits) in parsed:
        by_prefix[prefix].append((s, int(digits), len(digits)))

    results = [analyze_prefix(group_name, prefix, entries)
               for prefix, entries in sorted(by_prefix.items())]
    return results, ignored


def projected_unique(sheets, all_changes):
    """Safety net: confirm the projected sheet-number set (finals for changed sheets,
    originals for the rest) has no duplicates before touching the model. Guards the
    pathological case of one prefix shared across two selected groups."""
    new_by_id = {eid_value(s.Id): new for (s, old, new) in all_changes}
    seen = set()
    for s in sheets:
        num = new_by_id.get(eid_value(s.Id), s.SheetNumber)
        if num in seen:
            return False, num
        seen.add(num)
    return True, None


def apply_changes(all_changes, existing_numbers):
    """Collision-safe two-phase renumber in one transaction: every changing sheet is
    first parked on a unique temporary number, then set to its final number - so no
    two sheets ever hold the same number at any intermediate step."""
    existing = set(existing_numbers)
    temps, i = {}, 0
    for (s, old, new) in all_changes:
        while ("ZZZ-CORRECT-TMP-%d" % i) in existing:
            i += 1
        t = "ZZZ-CORRECT-TMP-%d" % i
        i += 1
        temps[eid_value(s.Id)] = t
        existing.add(t)

    t = Transaction(doc, "CORRECT sheet numbers")
    t.Start()
    try:
        for (s, old, new) in all_changes:
            s.SheetNumber = temps[eid_value(s.Id)]
        for (s, old, new) in all_changes:
            s.SheetNumber = new
        t.Commit()
    except Exception:
        t.RollBack()
        raise


def print_audit(all_results, all_ignored):
    output.print_md("# CORRECT - sheet numbering audit")
    for r in all_results:
        output.print_md("## %s  &nbsp; (prefix `%s`, %d sheets)"
                        % (r["group"], r["prefix"], r["count"]))
        issues = []
        if r["gaps"]:
            issues.append("gap(s) at %s" % ", ".join(str(g) for g in r["gaps"]))
        if r["duplicates"]:
            issues.append("duplicate value(s) %s"
                          % ", ".join(str(d) for d in r["duplicates"]))
        if r["malformed"]:
            issues.append("%d padding mismatch(es) -> width %d"
                          % (len(r["malformed"]), r["target_width"]))
        output.print_md("**Issues:** %s" % ("; ".join(issues) if issues else "none"))
        if r["changes"]:
            rows = [[old, "->", new] for (s, old, new) in r["changes"]]
            output.print_table(rows, columns=["Before", "", "After"])
        else:
            output.print_md("_No number changes needed._")
    if all_ignored:
        output.print_md("## Ignored (no trailing-number pattern)")
        for g, s in all_ignored:
            output.print_md("- [%s] `%s` - %s" % (g, s.SheetNumber, s.Name))


def toast(msg):
    try:
        forms.toast(msg, title="CORRECT")
    except Exception:
        output.print_md(msg)


# Confirmation dialog with the full BEFORE -> AFTER mapping embedded in a
# scrollable list, so approval never depends on the separate output window
# being readable (the WebBrowser-based output window can fail to paint before
# a modal dialog blocks the UI thread).
CONFIRM_XAML = (
    '<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
    'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" '
    'ShowInTaskbar="False" Width="560" Height="560" MinWidth="460" MinHeight="360" '
    'ResizeMode="CanResizeWithGrip" WindowStartupLocation="CenterScreen" '
    'Title="CORRECT - confirm renumber">'
    '<DockPanel Margin="12">'
    '<TextBlock x:Name="header_tb" DockPanel.Dock="Top" TextWrapping="Wrap" '
    'Margin="0,0,0,8"/>'
    '<StackPanel DockPanel.Dock="Bottom" Orientation="Horizontal" '
    'HorizontalAlignment="Right" Margin="0,10,0,0">'
    '<Button x:Name="cancel_btn" Content="Cancel" Width="90" Height="28" '
    'Margin="0,0,10,0" Click="cancel_clicked"/>'
    '<Button x:Name="ok_btn" Content="Apply renumber" Width="130" Height="28" '
    'Click="ok_clicked"/>'
    '</StackPanel>'
    '<TextBox x:Name="list_tb" IsReadOnly="True" FontFamily="Consolas" '
    'FontSize="13" VerticalScrollBarVisibility="Auto" '
    'HorizontalScrollBarVisibility="Auto" TextWrapping="NoWrap"/>'
    '</DockPanel></Window>'
)


class ConfirmWindow(forms.WPFWindow):
    """Modal confirm dialog carrying its own scrollable mapping list."""

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
    sheets = get_sheets()
    if not sheets:
        forms.alert("No sheets in this project.", exitscript=True)

    group_by_id = {eid_value(s.Id): sheet_group(s) for s in sheets}
    groups = sorted(set(g for g in group_by_id.values() if g))
    if not groups:
        forms.alert("No '%s' values found on sheets. CORRECT works on Sheet Groups."
                    % SHEET_GROUP_PARAM, exitscript=True)

    picked = forms.SelectFromList.show(
        [ALL_GROUPS] + groups,
        title="CORRECT - pick Sheet Group(s) to audit",
        button_name="Audit selected",
        multiselect=True)
    if not picked:
        script.exit()
    chosen = groups if ALL_GROUPS in picked else [g for g in picked if g in groups]

    all_results, all_ignored = [], []
    for g in chosen:
        in_group = [s for s in sheets if group_by_id[eid_value(s.Id)] == g]
        results, ignored = build_group_results(g, in_group)
        all_results.extend(results)
        all_ignored.extend((g, s) for s in ignored)

    all_changes = [c for r in all_results for c in r["changes"]]

    # Read-only audit is always shown first, whether or not there is anything to fix.
    print_audit(all_results, all_ignored)

    if not all_changes:
        output.print_md("---")
        output.print_md("**Result: no numbering issues found in %d group(s). "
                        "Nothing changed.**" % len(chosen))
        toast("No numbering issues found in %d group(s)." % len(chosen))
        script.exit()

    ok, conflict = projected_unique(sheets, all_changes)
    if not ok:
        forms.alert("Aborted - the correction would produce a duplicate sheet number "
                    "(%s). No changes made." % conflict, exitscript=True)

    lines = []
    for r in all_results:
        if not r["changes"]:
            continue
        lines.append("%s  (prefix %s)" % (r["group"], r["prefix"]))
        for (s, old, new) in r["changes"]:
            lines.append("    %s  ->  %s" % (old, new))
        lines.append("")
    confirm = ConfirmWindow(
        "Found %d sheet(s) to renumber across %d group(s). Review the full "
        "BEFORE -> AFTER mapping below. Sheet NAMES will not change - only "
        "numbers." % (len(all_changes), len(chosen)),
        lines)
    confirm.ShowDialog()
    proceed = confirm.confirmed
    if not proceed:
        output.print_md("---")
        output.print_md("**Result: cancelled by user. Nothing changed.**")
        toast("Cancelled - no changes made.")
        script.exit()

    apply_changes(all_changes, [s.SheetNumber for s in sheets])

    output.print_md("---")
    output.print_md("**Result: applied - %d sheet(s) renumbered. Sheet Names unchanged.**"
                    % len(all_changes))
    toast("Renumbered %d sheet(s)." % len(all_changes))


run()

# Mucus Plug Navigator

`MucusPlugNavigator` is a 3D Slicer scripted module for reviewing mucus plug
segmentations. It does not split, extract, or run connected components. Every
existing active segment in the selected segmentation node is treated as one
mucus plug.

The module embeds Slicer's standard `qMRMLSegmentEditorWidget`, so normal
Segment Editor behavior is reused instead of replacing Slicer's core Segment
Editor.

## Main Features

- Select a mucus `Segmentation` node and CT `Source volume` node.
- Count active mucus plug segments with `Mucus plug count: N`.
- Auto-jump to a segment when it is selected in the segment list.
- Navigate in segmentation order with `Last` and `Next`.
- Mark the segment you leave as completed when `Next` is clicked.
- Use adjustable `Jump zoom` when jumping to a selected segment.
- Show segment-name labels for mucus plugs visible in the current slice views.
- Hide or show the whole segmentation in both 2D and 3D with `Hide Seg`.
- Use `H` as a shortcut for `Hide Seg`.
- Use Left Arrow for `Last` and Right Arrow for `Next`.
- Keep visible editing controls focused on `No editing`, `Paint`, and `Erase`.
- Measure the selected segment only when `Measure` is clicked.
- Export active mucus plug measurements to CSV.
- Logically delete segments by moving them out of the active segmentation list.
- Restore logically deleted segments from the `Restore` dialog.
- Run a bundled dummy external model script for integration testing.

## Development Loading

In 3D Slicer:

1. Open `Edit > Application Settings > Modules`.
2. Add the module folder to additional module paths:
   `MucusPlugNavigator`
3. Restart Slicer or use the Developer Tools reload workflow.
4. Open `Mucus Plug Navigator` under the `Segmentation` category.

After code changes, use Slicer's module reload button. If keyboard shortcuts do
not refresh after repeated reloads, restart Slicer once to clear old shortcut
objects from the previous loaded version.

## Basic Workflow

1. Load the CT volume, for example `ROB0042-036-V2_0000`.
2. Load the mucus segmentation, for example `ROB0042-036-V2.nii.gz`.
3. In `Mucus Plug Navigator`, choose:
   - `Segmentation`: the mucus segmentation node
   - `Source volume`: the CT volume node
4. Select a segment in the segment list. The slice views jump to it
   automatically.
5. Use `Jump zoom` to control how close the jump view appears.
6. Use `Last` or `Next` to move through segments in segmentation order.
7. Edit the active segment with the embedded Segment Editor tools:
   - `No editing`
   - `Paint`
   - `Erase`

The manual `Jump` button is intentionally hidden in the UI because segment-list
selection now jumps automatically. The code is kept as a backup.

## Segment Name Labels

The module places markup labels near mucus plugs that intersect the current
slice views. If one slice contains several mucus plugs, each visible plug gets
its own label.

Label behavior:

- Labels use the segment name.
- Labels use the segment color.
- Labels grow with the current `Jump zoom` value.
- Labels are offset outside the visible plug when possible.
- Labels are restricted to the slice view where that plug is visible.
- Empty new segments do not show a label until pixels are painted into them.
- Hiding the segmentation also hides the segment-name labels.

The first label calculation after loading or changing data may be slower because
the module builds a cache of segment pixels. Later navigation is usually faster
because the cache is reused.

## Segment Status Flag

The rightmost flag column in the segment list is Slicer's segment status column.
The module uses it as a review marker:

- empty circle: not completed
- checkmark: completed

When `Next` is clicked, the module marks the segment you are leaving as
completed, then selects and jumps to the next active segment.

For best results, click the segment row/name before clicking `Next`. Clicking
directly on the circle/checkmark icon can change the status icon without always
making that row the active Segment Editor segment, depending on Slicer's table
behavior.

## Buttons

- `Add`: add a new segment to the selected segmentation.
- `Show 3D`: toggle 3D display for the selected segmentation.
- `Hide Seg`: hide or show the whole segmentation in 2D and 3D.
- `Delete`: logically delete only the selected mucus plug segment.
- `Measure`: calculate volume, length, and median CT value for the selected
  mucus plug.
- `No editing`: turn off the active Segment Editor effect.
- `Paint`: activate Segment Editor Paint.
- `Erase`: activate Segment Editor Erase.
- `Export`: save active mucus plug segment name, volume, and length to CSV.
- `Restore`: choose logically deleted mucus plug segments to restore.

`Delete` asks for confirmation and only affects the current segment. It does not
delete the CT volume or the whole segmentation node.

## Logical Delete And Restore

Logical delete removes the selected mucus plug from the active segmentation list
and stores it in a hidden backup segmentation node. This means deleted plugs do
not appear in the segment list, do not count toward `Mucus plug count`, and are
not included in export.

Use `Restore` to open a dialog listing deleted segments. Select one or more
segments and click `Restore selected` to move them back into the active
segmentation.

When a new segment is added, the module avoids reusing names that are waiting in
the restore list. For example, if active segments include `Segment_1` and
`Segment_3` through `Segment_10`, and deleted `Segment_2` is still restorable,
then a new added segment should become `Segment_11`.

## Measurement And Export

`Measure` calculates the selected segment only on demand. This keeps normal
navigation and editing faster.

Displayed values:

- `Volume`: number of non-zero pixels/voxels in the selected segment labelmap.
- `Length`: estimated main-axis length in pixels.
- `Median CT`: median source CT scalar value across all voxels inside the
  selected mucus plug.

`Export` calculates measurements for all active segments and writes a CSV file:

```csv
Mucus plug count,37

Segment,Volume,Length
Segment_-1,118,15
Segment_-2,141,12
```

Very large mask-like segments are skipped during export using the module's
configured mask threshold, so whole-mask rows are not written to the CSV. If the
CSV file is already open in Excel or another program, close it before exporting
again.

## Keyboard Shortcuts

- `H`: hide or show the whole selected segmentation.
- `Left Arrow`: go to the previous active segment.
- `Right Arrow`: go to the next active segment.

Shortcuts are intended to run only while `Mucus Plug Navigator` is active. If a
text field is focused, the shortcut may be ignored so typed text is not changed.

## Debug Output

The module prints label-navigation diagnostics to the Slicer console when
`SEGMENT_NAME_LABEL_DEBUG` is `True`.

Useful messages include:

- `Jump button all-view check`
- `Next button all-view check`
- `Last button all-view check`
- `Scroll Red single-view check`
- `Scroll Green single-view check`
- `Hide/Show Seg button`
- `Segmentation display observer`

These messages help identify whether time is spent in Slicer visibility changes,
label cache building, or slice-label refresh.

## Dummy Model Launch Test

The module includes `dummy_mucus_model.py` as a small test script. It does not
run the real mucus model. It only checks whether the module can start an
external Python script and read its output.

After loading the module in Slicer, open the Python console and run:

```python
widget = slicer.modules.mucusplugnavigator.widgetRepresentation().self()
result = widget.logic.runDummyMucusModelTest(caseID="test_from_slicer")
print(result["returnCode"])
print(result["stdout"])
print(result["stderr"])
```

`returnCode` should be `0`, and `stdout` should contain JSON with
`"status": "ok"`.

For a real PyTorch/MONAI mucus model, it may be better to pass the Python
executable from the model environment:

```python
result = widget.logic.runDummyMucusModelTest(
    pythonExecutable=r"C:\path\to\model_env\python.exe",
    caseID="test_from_model_env",
)
```

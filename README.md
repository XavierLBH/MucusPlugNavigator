# Mucus Plug Navigator

`MucusPlugNavigator` is a 3D Slicer scripted module for reviewing mucus plug
segmentations. It does not split, extract, or run connected components. Every
existing active segment in the selected segmentation node is treated as one
mucus plug.

The module embeds Slicer's standard `qMRMLSegmentEditorWidget`, so normal
Segment Editor behavior is reused instead of replacing the core Segment Editor.

## What It Adds

- Select a mucus `Segmentation` node and CT `Source volume` node.
- Count active mucus plug segments with `Mucus plug count: N`.
- Auto-jump to a segment when it is selected in the segment list.
- Navigate in segmentation order with `Last` and `Next`.
- Mark the segment you leave as completed when `Next` is clicked.
- Use an adjustable `Jump zoom` value when jumping to a selected segment.
- Show the selected segment name near the mucus plug after a successful jump.
- Toggle whole-segmentation visibility in both 2D and 3D with `Hide Seg`.
- Use the `H` key as a shortcut for `Hide Seg`.
- Use Left Arrow for `Last` and Right Arrow for `Next`.
- Keep the visible editing controls focused on `No editing`, `Paint`, and `Erase`.
- Measure the selected segment only when `Measure` is clicked.
- Export active mucus plug measurements to CSV.
- Logically delete segments by moving them out of the active segmentation list.
- Restore logically deleted segments from the `Restore` dialog.

## Development Loading

In 3D Slicer:

1. Open `Edit > Application Settings > Modules`.
2. Add this module path to additional module paths:
   `MucusPlugNavigator`
3. Restart Slicer or use the Developer Tools reload workflow.
4. Open `Mucus Plug Navigator` under the `Segmentation` category.

After code changes, use Slicer's module reload button. If keyboard shortcuts do
not refresh after repeated reloads, restart Slicer once to clear old shortcut
objects from the previous loaded version.

## Use

1. Load the CT volume, for example `ROB0042-036-V2_0000`.
2. Load the mucus segmentation, for example `ROB0042-036-V2.nii.gz`.
3. In `Mucus Plug Navigator`, choose:
   - `Segmentation`: the mucus segmentation node
   - `Source volume`: the CT volume node
4. Select a segment in the segment list. The slice views jump to it automatically.
5. Use `Jump zoom` to control how close the jump view appears.
6. Use `Last` or `Next` to move through segments in segmentation order.
   `Next` also marks the selected/current segment as completed in Slicer's
   segment status/flag column before moving forward.
7. Use Segment Editor normally with:
   - `No editing`
   - `Paint`
   - `Erase`

The manual `Jump` button is intentionally hidden in the UI because segment
selection now jumps automatically. The code is kept as a backup.

After a successful jump, the module places a small markup label near the current
segment. The label is offset outside the segment bounds so it is less likely to
cover the mucus plug itself. The label uses the segment color, grows with the
current `Jump zoom` value, and uses Markups slice projection so it can remain
visible while scrolling nearby slices. Empty new segments do not show a label
until pixels are painted into them.

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
- `Delete`: logically delete only the selected mucus plug segment after confirmation.
- `Measure`: calculate volume and length for the selected mucus plug.
- `No editing`: turn off the active Segment Editor effect.
- `Paint`: activate Segment Editor Paint.
- `Erase`: activate Segment Editor Erase.
- `Export`: save active mucus plug segment name, volume, and length to CSV.
- `Restore`: choose logically deleted mucus plug segments to restore.


## Measurement And Export

`Measure` calculates the selected segment only on demand. This keeps normal
navigation and editing faster.

The displayed values are:

- `Volume`: number of non-zero pixels/voxels in the selected segment labelmap.
- `Length`: estimated main-axis length in pixels.

`Export` calculates measurements for all active segments and writes a CSV file:

```csv
Mucus plug count,37

Segment,Volume,Length
Segment_-1,118,15
Segment_-2,141,12
```

Very large mask-like segments are skipped during export using the module's
configured mask threshold, so whole-mask rows are not written to the CSV.

## Keyboard Shortcuts

- `H`: hide or show the whole selected segmentation.
- `Left Arrow`: go to the previous active segment.
- `Right Arrow`: go to the next active segment.

Shortcuts are only intended to run while `Mucus Plug Navigator` is active. If a
text field is focused, the shortcut may be ignored so typed text is not changed.

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


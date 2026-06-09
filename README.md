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
- Use an adjustable `Jump zoom` value when jumping to a selected segment.
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
7. Use Segment Editor normally with:
   - `No editing`
   - `Paint`
   - `Erase`

The manual `Jump` button is intentionally hidden in the UI because segment
selection now jumps automatically. The code is kept as a backup.

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


## Keyboard Shortcuts

- `H`: hide or show the whole selected segmentation.
- `Left Arrow`: go to the previous active segment.
- `Right Arrow`: go to the next active segment.

Shortcuts are only intended to run while `Mucus Plug Navigator` is active. If a
text field is focused, the shortcut may be ignored so typed text is not changed.


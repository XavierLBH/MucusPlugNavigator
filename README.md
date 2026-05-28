# Mucus Plug Navigator

`MucusPlugNavigator` is a 3D Slicer scripted module for the updated mucus plug workflow.
It does not split, extract, or run connected components. Every existing segment in the
selected segmentation node is treated as one mucus plug.

## What It Adds

- Embeds Slicer's standard `qMRMLSegmentEditorWidget`
- Reuses normal Segment Editor behavior, with visible editing controls limited to No editing, Paint, and Erase
- Adds `Jump`, `Last`, `Next`, and `Delete` controls
- Shows `Mucus plug count: N`
- Calculates current segment `Volume` and `Length` only when `Measure` is clicked
- Exports all mucus plug segment measurements to a CSV file
- Jumps slice views to the selected segment center
- Applies an adjustable jump zoom factor from `1x` to `10x`
- Places `Add`, `Show 3D`, `Delete`, `Measure`, `No editing`, `Paint`, `Erase`, and `Export CSV` together in one custom toolbar row

## Development Loading

In 3D Slicer:

1. Open `Edit > Application Settings > Modules`.
2. Add this module path to additional module paths:
   `MucusPlugNavigator`
3. Restart Slicer or use the Developer Tools reload workflow.
4. Open `Mucus Plug Navigator` under the `Segmentation` category.

## Use

1. Load the CT volume.
2. Load the mucus segmentation.
3. In `Mucus Plug Navigator`, choose:
   - `Segmentation`: the mucus segmentation node
   - `Source volume`: the CT volume node
4. Use Segment Editor normally.
5. Use:
   - `Jump` to center and zoom to the selected segment
   - `Last` to move to the previous segment in segmentation order
   - `Next` to move to the next segment in segmentation order
   - `Delete` to delete only the selected segment after confirmation
   - `Measure` to calculate volume and length for the selected segment
   - `Export CSV` to save all segment names, volumes, and lengths
6. Use the visible Segment Editor effect buttons for:
   - `No editing`
   - `Paint`
   - `Erase`

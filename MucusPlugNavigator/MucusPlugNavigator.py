import csv
import logging

import qt
import slicer
import vtk
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin


#
# Constants
#


SEGMENT_EDITOR_SINGLETON_TAG = "MucusPlugNavigatorSegmentEditor"
SEGMENT_EDITOR_NODE_NAME = "MucusPlugNavigatorSegmentEditor"

BUTTON_MINIMUM_WIDTH = 92
BUTTON_MINIMUM_HEIGHT = 32

JUMP_ZOOM_MINIMUM = 1.0
JUMP_ZOOM_MAXIMUM = 10.0
JUMP_ZOOM_DECIMALS = 1
JUMP_ZOOM_STEP = 0.5
JUMP_ZOOM_DEFAULT = 1.0

ROW_MARGINS = (0, 4, 0, 4)
GRID_HORIZONTAL_SPACING = 6
GRID_VERTICAL_SPACING = 4


#
# MucusPlugNavigator
#


class MucusPlugNavigator(ScriptedLoadableModule):
    """Navigate, inspect, edit, delete, measure, export, and count existing mucus plug segments."""

    def __init__(self, parent):
        """Initialize the scripted module metadata shown by 3D Slicer."""
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "Mucus Plug Navigator"
        self.parent.categories = ["Segmentation"]
        self.parent.dependencies = ["SegmentEditor"]
        self.parent.contributors = ["Codex"]
        self.parent.helpText = """
Use the standard Segment Editor tools while adding mucus-plug navigation controls.
Select an existing mucus segmentation and CT source volume, then use Jump, Next,
and Delete to inspect existing segments. Each segment is treated as one mucus plug.
"""
        self.parent.acknowledgementText = """
This module embeds Slicer's qMRMLSegmentEditorWidget and adds navigation controls.
"""


#
# MucusPlugNavigatorWidget
#


class MucusPlugNavigatorWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Build and manage the Mucus Plug Navigator user interface."""

    def __init__(self, parent=None):
        """Create widget state without touching the MRML scene yet."""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)

        self.logic = MucusPlugNavigatorLogic()
        self.segmentEditorNode = None
        self.segmentEditorWidget = None

        self._observedSegmentation = None
        self._segmentationObserverTags = []
        self._sliceBaseFieldOfViewByID = {}
        self._segmentEditorAddButton = None
        self._segmentEditorShow3DButton = None

        self._initializeWidgetReferences()

    def setup(self):
        """Build the full module UI and connect it to the current Slicer scene."""
        ScriptedLoadableModuleWidget.setup(self)

        self.segmentEditorNode = self._getOrCreateSegmentEditorNode()

        self._buildSelectorsSection()
        self._buildNavigationSection()
        self._buildActionToolbarSection()
        self._buildEmbeddedSegmentEditorSection()
        self._connectSignals()
        self._initializeWidgetState()

    def cleanup(self):
        """Release observers and Segment Editor view hooks when Slicer unloads the module."""
        self._removeSegmentationObservers()
        if self.segmentEditorWidget:
            self.segmentEditorWidget.setActiveEffect(None)
            self.segmentEditorWidget.removeViewObservations()
            self.segmentEditorWidget.uninstallKeyboardShortcuts()
            self.segmentEditorWidget = None

    def enter(self):
        """Install Segment Editor view hooks when the module becomes active."""
        if self.segmentEditorWidget:
            self.segmentEditorWidget.setMRMLScene(slicer.mrmlScene)
            self.segmentEditorWidget.setupViewObservations()
            self.segmentEditorWidget.installKeyboardShortcuts()
            self.updateSegmentCountAndButtons()

    def exit(self):
        """Remove Segment Editor view hooks when the user leaves the module."""
        if self.segmentEditorWidget:
            self.segmentEditorWidget.setActiveEffect(None)
            self.segmentEditorWidget.removeViewObservations()
            self.segmentEditorWidget.uninstallKeyboardShortcuts()

    def _initializeWidgetReferences(self):
        """Declare widget attributes in one place so later code is easy to scan."""
        self.segmentationSelector = None
        self.sourceVolumeSelector = None

        self.countLabel = None
        self.volumeLabel = None
        self.lengthLabel = None
        self.zoomSpinBox = None

        self.jumpButton = None
        self.lastButton = None
        self.nextButton = None

        self.addButton = None
        self.show3DButton = None
        self.deleteButton = None
        self.measureButton = None
        self.noEditingButton = None
        self.paintButton = None
        self.eraseButton = None
        self.exportButton = None

    def _buildSelectorsSection(self):
        """Create the segmentation and source-volume selectors."""
        selectorsFrame = qt.QFrame()
        selectorsLayout = qt.QFormLayout()
        selectorsFrame.setLayout(selectorsLayout)
        selectorsLayout.setContentsMargins(0, 0, 0, 0)

        self.segmentationSelector = self._createNodeSelector(
            ["vtkMRMLSegmentationNode"],
            "Select the mucus segmentation. Each segment is treated as one mucus plug.",
        )
        selectorsLayout.addRow("Segmentation:", self.segmentationSelector)

        self.sourceVolumeSelector = self._createNodeSelector(
            ["vtkMRMLScalarVolumeNode"],
            "Select the CT source volume used by Segment Editor.",
        )
        selectorsLayout.addRow("Source volume:", self.sourceVolumeSelector)

        self.layout.addWidget(selectorsFrame)

    def _buildNavigationSection(self):
        """Create count, measurement labels, jump zoom, and navigation buttons."""
        controlsFrame = qt.QFrame()
        controlsLayout = qt.QGridLayout()
        controlsFrame.setLayout(controlsLayout)
        self._configureGridLayout(controlsLayout)

        self.countLabel = qt.QLabel("Mucus plug count: 0")
        self.countLabel.setMinimumWidth(150)
        controlsLayout.addWidget(self.countLabel, 0, 0)

        self.volumeLabel = qt.QLabel("Volume: - pixels")
        controlsLayout.addWidget(self.volumeLabel, 0, 1)

        self.lengthLabel = qt.QLabel("Length: - pixels")
        controlsLayout.addWidget(self.lengthLabel, 0, 2)

        zoomLabel = qt.QLabel("Jump zoom:")
        controlsLayout.addWidget(zoomLabel, 1, 0)

        self.zoomSpinBox = qt.QDoubleSpinBox()
        self.zoomSpinBox.setRange(JUMP_ZOOM_MINIMUM, JUMP_ZOOM_MAXIMUM)
        self.zoomSpinBox.setDecimals(JUMP_ZOOM_DECIMALS)
        self.zoomSpinBox.setSingleStep(JUMP_ZOOM_STEP)
        self.zoomSpinBox.setSuffix("x")
        self.zoomSpinBox.setValue(JUMP_ZOOM_DEFAULT)
        self.zoomSpinBox.setToolTip("Higher zoom uses a smaller slice field of view after jumping.")
        controlsLayout.addWidget(self.zoomSpinBox, 1, 1)

        self.jumpButton = self._createButton("Jump", "Jump slice views to the currently selected mucus plug.")
        controlsLayout.addWidget(self.jumpButton, 1, 2)

        self.lastButton = self._createButton("Last", "Select the previous segment in segmentation order and jump to it.")
        controlsLayout.addWidget(self.lastButton, 1, 3)

        self.nextButton = self._createButton("Next", "Select the next segment in segmentation order and jump to it.")
        controlsLayout.addWidget(self.nextButton, 1, 4)

        self._setNavigationButtonIcons()
        self.layout.addWidget(controlsFrame)

    def _buildActionToolbarSection(self):
        """Create the compact action toolbar for segment and edit commands."""
        segmentToolbarFrame = qt.QFrame()
        segmentToolbarLayout = qt.QGridLayout()
        segmentToolbarFrame.setLayout(segmentToolbarLayout)
        self._configureGridLayout(segmentToolbarLayout)

        self.addButton = self._createButton("Add", "Add a new segment to the selected segmentation.")
        segmentToolbarLayout.addWidget(self.addButton, 0, 0)

        self.show3DButton = self._createButton("Show 3D", "Toggle 3D display for the selected segmentation.")
        segmentToolbarLayout.addWidget(self.show3DButton, 0, 1)

        self.deleteButton = self._createButton("Delete", "Delete only the currently selected mucus plug segment.")
        segmentToolbarLayout.addWidget(self.deleteButton, 0, 2)

        self.measureButton = self._createButton("Measure", "Calculate volume and length for the currently selected mucus plug.")
        segmentToolbarLayout.addWidget(self.measureButton, 0, 3)

        self.noEditingButton = self._createButton("No editing", "Turn off the active Segment Editor effect.")
        segmentToolbarLayout.addWidget(self.noEditingButton, 1, 0)

        self.paintButton = self._createButton("Paint", "Activate the Segment Editor Paint effect.")
        segmentToolbarLayout.addWidget(self.paintButton, 1, 1)

        self.eraseButton = self._createButton("Erase", "Activate the Segment Editor Erase effect.")
        segmentToolbarLayout.addWidget(self.eraseButton, 1, 2)

        self.exportButton = self._createButton("Export CSV", "Export segment name, volume, and length for all mucus plugs to a CSV file.")
        segmentToolbarLayout.addWidget(self.exportButton, 1, 3)

        self.layout.addWidget(segmentToolbarFrame)

    def _buildEmbeddedSegmentEditorSection(self):
        """Create and configure the standard Slicer Segment Editor widget."""
        self.segmentEditorWidget = slicer.qMRMLSegmentEditorWidget()
        self.segmentEditorWidget.setMRMLScene(slicer.mrmlScene)
        self.segmentEditorWidget.setMRMLSegmentEditorNode(self.segmentEditorNode)
        self._configureEmbeddedSegmentEditor()
        self.layout.addWidget(self.segmentEditorWidget)
        self._prepareCustomSegmentToolbar()

    def _connectSignals(self):
        """Connect all Qt signals after every widget has been created."""
        self.segmentationSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.onSegmentationNodeChanged)
        self.sourceVolumeSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.onSourceVolumeNodeChanged)

        self.jumpButton.connect("clicked(bool)", self.onJumpButton)
        self.lastButton.connect("clicked(bool)", self.onLastButton)
        self.nextButton.connect("clicked(bool)", self.onNextButton)
        self.addButton.connect("clicked(bool)", self.onAddButton)
        self.show3DButton.connect("clicked(bool)", self.onShow3DButton)
        self.deleteButton.connect("clicked(bool)", self.onDeleteButton)
        self.measureButton.connect("clicked(bool)", self.onMeasureButton)
        self.noEditingButton.connect("clicked(bool)", self.onNoEditingButton)
        self.paintButton.connect("clicked(bool)", self.onPaintButton)
        self.eraseButton.connect("clicked(bool)", self.onEraseButton)
        self.exportButton.connect("clicked(bool)", self.onExportButton)

        self.zoomSpinBox.connect("valueChanged(double)", self.onZoomChanged)
        self.segmentEditorWidget.connect("currentSegmentIDChanged(QString)", self.onCurrentSegmentChanged)

    def _initializeWidgetState(self):
        """Synchronize the widget with any nodes already selected in the scene."""
        self.onSegmentationNodeChanged(self.segmentationSelector.currentNode())
        self.onSourceVolumeNodeChanged(self.sourceVolumeSelector.currentNode())
        self.updateSegmentCountAndButtons()

    def _createNodeSelector(self, nodeTypes, toolTip):
        """Create a Slicer node selector with consistent module settings."""
        selector = slicer.qMRMLNodeComboBox()
        selector.nodeTypes = nodeTypes
        selector.selectNodeUponCreation = True
        selector.addEnabled = False
        selector.removeEnabled = False
        selector.noneEnabled = True
        selector.showHidden = False
        selector.showChildNodeTypes = False
        selector.setMRMLScene(slicer.mrmlScene)
        selector.setToolTip(toolTip)
        return selector

    def _createButton(self, text, toolTip):
        """Create a compact button with text and a tooltip."""
        button = qt.QPushButton(text)
        button.setToolTip(toolTip)
        return button

    def _configureGridLayout(self, layout):
        """Apply the module's compact grid spacing to a layout."""
        layout.setContentsMargins(*ROW_MARGINS)
        layout.setHorizontalSpacing(GRID_HORIZONTAL_SPACING)
        layout.setVerticalSpacing(GRID_VERTICAL_SPACING)

    def _getOrCreateSegmentEditorNode(self):
        """Return the singleton Segment Editor parameter node used by this module."""
        node = slicer.mrmlScene.GetSingletonNode(SEGMENT_EDITOR_SINGLETON_TAG, "vtkMRMLSegmentEditorNode")
        if node:
            return node

        node = slicer.mrmlScene.CreateNodeByClass("vtkMRMLSegmentEditorNode")
        node.UnRegister(None)
        node.SetSingletonTag(SEGMENT_EDITOR_SINGLETON_TAG)
        node.SetName(SEGMENT_EDITOR_NODE_NAME)
        slicer.mrmlScene.AddNode(node)
        return node

    def _configureEmbeddedSegmentEditor(self):
        """Hide duplicated Segment Editor controls and keep only the needed edit effects."""
        if hasattr(self.segmentEditorWidget, "setSegmentationNodeSelectorVisible"):
            self.segmentEditorWidget.setSegmentationNodeSelectorVisible(False)
        if hasattr(self.segmentEditorWidget, "setSourceVolumeNodeSelectorVisible"):
            self.segmentEditorWidget.setSourceVolumeNodeSelectorVisible(False)
        elif hasattr(self.segmentEditorWidget, "setMasterVolumeNodeSelectorVisible"):
            self.segmentEditorWidget.setMasterVolumeNodeSelectorVisible(False)

        if hasattr(self.segmentEditorWidget, "setJumpToSelectedSegmentEnabled"):
            self.segmentEditorWidget.setJumpToSelectedSegmentEnabled(False)
        if hasattr(self.segmentEditorWidget, "setSwitchToSegmentationsButtonVisible"):
            self.segmentEditorWidget.setSwitchToSegmentationsButtonVisible(False)
        if hasattr(self.segmentEditorWidget, "setSpecifyGeometryButtonVisible"):
            self.segmentEditorWidget.setSpecifyGeometryButtonVisible(False)
        if hasattr(self.segmentEditorWidget, "setEffectNameOrder"):
            self.segmentEditorWidget.setEffectNameOrder(["Paint", "Erase"])
        if hasattr(self.segmentEditorWidget, "setUnorderedEffectsVisible"):
            self.segmentEditorWidget.setUnorderedEffectsVisible(False)
        if hasattr(self.segmentEditorWidget, "setEffectButtonStyle"):
            self.segmentEditorWidget.setEffectButtonStyle(qt.Qt.ToolButtonTextBesideIcon)
        if hasattr(self.segmentEditorWidget, "setEffectColumnCount"):
            self.segmentEditorWidget.setEffectColumnCount(2)
        if hasattr(self.segmentEditorWidget, "updateEffectList"):
            self.segmentEditorWidget.updateEffectList()
        if hasattr(self.segmentEditorWidget, "setAutoShowSourceVolumeNode"):
            self.segmentEditorWidget.setAutoShowSourceVolumeNode(True)
        elif hasattr(self.segmentEditorWidget, "setAutoShowMasterVolumeNode"):
            self.segmentEditorWidget.setAutoShowMasterVolumeNode(True)

    def _prepareCustomSegmentToolbar(self):
        """Copy icons from Segment Editor controls, then hide the original controls."""
        addButton = self._findSegmentEditorButton("Add")
        show3DButton = self._findSegmentEditorButton("Show 3D")
        removeButton = self._findSegmentEditorButton("Remove")
        noneButton = self._findSegmentEditorButton("None")
        paintButton = self._findSegmentEditorButton("Paint")
        eraseButton = self._findSegmentEditorButton("Erase")

        self._segmentEditorAddButton = addButton
        self._segmentEditorShow3DButton = show3DButton

        self._copyButtonIconSize(addButton, self.addButton)
        self._copyButtonIconSize(show3DButton, self.show3DButton)
        self._copyButtonIconSize(removeButton if removeButton else addButton, self.deleteButton)
        self._copyButtonIconSize(addButton, self.measureButton)
        self._copyButtonIconSize(noneButton if noneButton else addButton, self.noEditingButton)
        self._copyButtonIconSize(paintButton if paintButton else addButton, self.paintButton)
        self._copyButtonIconSize(eraseButton if eraseButton else addButton, self.eraseButton)
        self._copyButtonIconSize(addButton, self.exportButton)
        self._copyButtonIconSize(addButton, self.jumpButton)
        self._copyButtonIconSize(addButton, self.lastButton)
        self._copyButtonIconSize(addButton, self.nextButton)

        self._copyButtonIcon(addButton, self.addButton)
        self._copyButtonIcon(show3DButton, self.show3DButton)
        self._copyButtonIcon(removeButton, self.deleteButton)
        self._copyButtonIcon(noneButton, self.noEditingButton)
        self._copyButtonIcon(paintButton, self.paintButton)
        self._copyButtonIcon(eraseButton, self.eraseButton)

        self._normalizeButtonSizePolicies()
        self._hideSegmentEditorToolbarButtons()
        self._hideSegmentEditorEffectButtons()

    def _copyButtonIcon(self, sourceButton, targetButton):
        """Copy an icon from a hidden Segment Editor button to a custom button."""
        if not sourceButton:
            return
        try:
            icon = sourceButton.icon
            if callable(icon):
                icon = icon()
            targetButton.setIcon(icon)
        except Exception:
            logging.debug("Could not copy Segment Editor effect icon", exc_info=True)

    def _copyButtonIconSize(self, sourceButton, targetButton):
        """Copy only the icon size from a Segment Editor button, avoiding layout size constraints."""
        if not sourceButton:
            return
        try:
            targetButton.setIconSize(sourceButton.iconSize)
        except Exception:
            logging.debug("Could not copy Segment Editor toolbar button icon size", exc_info=True)

    def _hideSegmentEditorToolbarButtons(self):
        """Hide the original Add, Remove, and Show 3D controls inside Segment Editor."""
        for buttonText in ("Add", "Remove", "Show 3D"):
            button = self._findSegmentEditorButton(buttonText)
            if button:
                button.hide()
        if hasattr(self.segmentEditorWidget, "setAddRemoveSegmentButtonsVisible"):
            self.segmentEditorWidget.setAddRemoveSegmentButtonsVisible(False)
        if hasattr(self.segmentEditorWidget, "setShow3DButtonVisible"):
            self.segmentEditorWidget.setShow3DButtonVisible(False)

    def _hideSegmentEditorEffectButtons(self):
        """Hide the original effect grid so only the custom No editing, Paint, and Erase buttons are visible."""
        effectButtonTexts = {
            "None",
            "Paint",
            "Erase",
            "Draw",
            "Scissors",
            "Fill between slices",
            "Grow from seeds",
            "Hollow",
            "Islands",
            "Level tracing",
            "Logical operators",
            "Margin",
            "Mask volume",
            "Smoothing",
            "Threshold",
        }
        effectButtons = [
            button
            for button in self.segmentEditorWidget.findChildren(qt.QAbstractButton)
            if self._buttonText(button) in effectButtonTexts
        ]

        for button in effectButtons:
            button.hide()

        self._hideMostLikelyEffectGrid(effectButtons)

    def _hideMostLikelyEffectGrid(self, effectButtons):
        """Hide the parent widget that most likely owns the original Segment Editor effect grid."""
        parentCounts = {}
        for button in effectButtons:
            parent = button.parent()
            if parent:
                parentCounts[parent] = parentCounts.get(parent, 0) + 1
        if not parentCounts:
            return

        mostLikelyEffectGrid = max(parentCounts, key=parentCounts.get)
        containsToolbarButton = any(
            self._buttonText(button) in ("Add", "Remove", "Show 3D")
            for button in mostLikelyEffectGrid.findChildren(qt.QAbstractButton)
        )
        if parentCounts[mostLikelyEffectGrid] >= 2 and mostLikelyEffectGrid != self.segmentEditorWidget and not containsToolbarButton:
            mostLikelyEffectGrid.hide()

    def _normalizeButtonSizePolicies(self):
        """Give all custom action buttons the same compact size policy."""
        for button in self._allActionButtons():
            button.setMinimumSize(qt.QSize(BUTTON_MINIMUM_WIDTH, BUTTON_MINIMUM_HEIGHT))
            button.setMaximumSize(qt.QSize(16777215, 16777215))
            button.setSizePolicy(qt.QSizePolicy.Preferred, qt.QSizePolicy.Fixed)

    def _allActionButtons(self):
        """Return every custom push button that should share the same compact size."""
        return (
            self.jumpButton,
            self.lastButton,
            self.nextButton,
            self.addButton,
            self.show3DButton,
            self.deleteButton,
            self.measureButton,
            self.noEditingButton,
            self.paintButton,
            self.eraseButton,
            self.exportButton,
        )

    def _findSegmentEditorButton(self, buttonText):
        """Find a child button inside the embedded Segment Editor by visible text."""
        for button in self.segmentEditorWidget.findChildren(qt.QAbstractButton):
            if self._buttonText(button) == buttonText:
                return button
        return None

    def _buttonText(self, button):
        """Return normalized button text without Qt accelerator markers."""
        text = ""
        if hasattr(button, "text"):
            text = button.text
            if callable(text):
                text = text()
        return str(text).replace("&", "").strip()

    def _setNavigationButtonIcons(self):
        """Add standard arrow icons to the previous and next navigation buttons."""
        try:
            style = slicer.util.mainWindow().style()
            self.lastButton.setIcon(style.standardIcon(qt.QStyle.SP_ArrowLeft))
            self.nextButton.setIcon(style.standardIcon(qt.QStyle.SP_ArrowRight))
        except Exception:
            logging.debug("Could not set standard button icons", exc_info=True)

    def onSegmentationNodeChanged(self, segmentationNode):
        """Update Segment Editor and observers when the selected segmentation changes."""
        self.segmentEditorWidget.setSegmentationNode(segmentationNode)
        self.logic.ensureSegmentationVisible(segmentationNode)
        self._observeSegmentation(segmentationNode)
        self._selectFirstSegmentIfNeeded()
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()

    def onSourceVolumeNodeChanged(self, sourceVolumeNode):
        """Update Segment Editor and slice background when the source CT volume changes."""
        if hasattr(self.segmentEditorWidget, "setSourceVolumeNode"):
            self.segmentEditorWidget.setSourceVolumeNode(sourceVolumeNode)
        else:
            self.segmentEditorWidget.setMasterVolumeNode(sourceVolumeNode)
        self.logic.ensureSourceVolumeVisible(sourceVolumeNode)
        self._sliceBaseFieldOfViewByID = {}
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()

    def onCurrentSegmentChanged(self, segmentID):
        """Clear stale measurements and refresh buttons when the active segment changes."""
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()

    def onObservedSegmentationChanged(self, caller=None, event=None):
        """Refresh the UI when segments are added, removed, reordered, or modified."""
        self._selectFirstSegmentIfNeeded()
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()

    def onZoomChanged(self, value):
        """Reapply jump zoom to the current segment when the zoom value changes."""
        segmentID = self.currentSegmentID()
        if self.logic.isValidSegmentID(self.segmentationNode(), segmentID):
            self.jumpToSegment(segmentID)

    def onJumpButton(self, checked=False):
        """Jump slice views to the currently selected segment."""
        segmentID = self.currentSegmentID()
        if not self.logic.isValidSegmentID(self.segmentationNode(), segmentID):
            self._selectFirstSegmentIfNeeded()
            segmentID = self.currentSegmentID()
        self.jumpToSegment(segmentID)

    def onLastButton(self, checked=False):
        """Select the previous segment in segmentation order and jump to it."""
        segmentationNode = self.segmentationNode()
        previousSegmentID = self.logic.previousSegmentID(segmentationNode, self.currentSegmentID(), wrap=True)
        if not previousSegmentID:
            return
        self.segmentEditorWidget.setCurrentSegmentID(previousSegmentID)
        self.jumpToSegment(previousSegmentID)
        self.updateSegmentCountAndButtons()

    def onNextButton(self, checked=False):
        """Select the next segment in segmentation order and jump to it."""
        segmentationNode = self.segmentationNode()
        nextSegmentID = self.logic.nextSegmentID(segmentationNode, self.currentSegmentID(), wrap=True)
        if not nextSegmentID:
            return
        self.segmentEditorWidget.setCurrentSegmentID(nextSegmentID)
        self.jumpToSegment(nextSegmentID)
        self.updateSegmentCountAndButtons()

    def onAddButton(self, checked=False):
        """Add a segment using Segment Editor behavior, with a direct fallback."""
        if self._segmentEditorAddButton:
            self._segmentEditorAddButton.click()
        else:
            segmentationNode = self.segmentationNode()
            if segmentationNode:
                segmentID = segmentationNode.GetSegmentation().AddEmptySegment()
                self.segmentEditorWidget.setCurrentSegmentID(segmentID)
        self.updateSegmentCountAndButtons()

    def onShow3DButton(self, checked=False):
        """Toggle 3D visibility using Segment Editor behavior, with a direct fallback."""
        if self._segmentEditorShow3DButton:
            self._segmentEditorShow3DButton.click()
            return

        segmentationNode = self.segmentationNode()
        if not segmentationNode:
            return
        segmentationNode.CreateDefaultDisplayNodes()
        displayNode = segmentationNode.GetDisplayNode()
        if displayNode and hasattr(displayNode, "SetVisibility3D"):
            displayNode.SetVisibility3D(not displayNode.GetVisibility3D())

    def onDeleteButton(self, checked=False):
        """Delete only the current segment after user confirmation."""
        segmentationNode = self.segmentationNode()
        segmentID = self.currentSegmentID()
        if not self.logic.isValidSegmentID(segmentationNode, segmentID):
            return

        segment = segmentationNode.GetSegmentation().GetSegment(segmentID)
        segmentName = segment.GetName() if segment else segmentID
        answer = qt.QMessageBox.question(
            slicer.util.mainWindow(),
            "Delete mucus plug segment",
            "Are you sure you want to delete this mucus plug segment?\n\n{}".format(segmentName),
            qt.QMessageBox.Yes | qt.QMessageBox.No,
            qt.QMessageBox.No,
        )
        if answer != qt.QMessageBox.Yes:
            return

        nextSegmentID = self.logic.deleteSegmentAndGetNearby(segmentationNode, segmentID)
        self.segmentEditorWidget.setCurrentSegmentID(nextSegmentID if nextSegmentID else "")
        self.updateSegmentCountAndButtons()
        if nextSegmentID:
            self.jumpToSegment(nextSegmentID)

    def onMeasureButton(self, checked=False):
        """Calculate volume and length for the current segment only when requested."""
        segmentationNode = self.segmentationNode()
        segmentID = self.currentSegmentID()
        if not self.logic.isValidSegmentID(segmentationNode, segmentID):
            self.resetCurrentSegmentMeasurements()
            return

        self.volumeLabel.setText("Volume: calculating...")
        self.lengthLabel.setText("Length: calculating...")
        slicer.app.processEvents()

        metrics = self.logic.segmentVoxelMetrics(segmentationNode, segmentID, self.sourceVolumeNode())
        if not metrics:
            self.volumeLabel.setText("Volume: failed")
            self.lengthLabel.setText("Length: failed")
            return

        self.volumeLabel.setText("Volume: {} pixels".format(metrics["volumePixels"]))
        self.lengthLabel.setText("Length: {} pixels".format(metrics["lengthPixels"]))

    def onNoEditingButton(self, checked=False):
        """Deactivate the current Segment Editor effect."""
        self.segmentEditorWidget.setActiveEffect(None)

    def onPaintButton(self, checked=False):
        """Activate the Segment Editor Paint effect."""
        self.segmentEditorWidget.setActiveEffectByName("Paint")

    def onEraseButton(self, checked=False):
        """Activate the Segment Editor Erase effect."""
        self.segmentEditorWidget.setActiveEffectByName("Erase")

    def onExportButton(self, checked=False):
        """Export all segment names, volumes, and lengths to a CSV file."""
        segmentationNode = self.segmentationNode()
        segmentIDs = self.logic.segmentIDs(segmentationNode)
        if not segmentIDs:
            slicer.util.warningDisplay("No mucus plug segments to export.")
            return

        filePath = self._promptForExportPath(segmentationNode.GetName())
        if not filePath:
            return

        self._setExportInProgress(True)
        try:
            self._writeMeasurementsCsv(filePath, segmentationNode)
            slicer.util.infoDisplay("Exported mucus plug measurements to:\n{}".format(filePath))
        except Exception as exc:
            logging.exception("Failed to export mucus plug measurements")
            slicer.util.errorDisplay("Failed to export mucus plug measurements:\n{}".format(exc))
        finally:
            self._setExportInProgress(False)

    def _promptForExportPath(self, segmentationName):
        """Ask the user where to save the CSV file and normalize the file extension."""
        defaultFileName = "{}_mucus_plugs.csv".format(segmentationName)
        filePath = qt.QFileDialog.getSaveFileName(
            slicer.util.mainWindow(),
            "Export mucus plug measurements",
            defaultFileName,
            "CSV files (*.csv)",
        )
        if not filePath:
            return ""
        if isinstance(filePath, tuple):
            filePath = filePath[0]
        filePath = str(filePath)
        if filePath and not filePath.lower().endswith(".csv"):
            filePath += ".csv"
        return filePath

    def _setExportInProgress(self, exporting):
        """Temporarily disable the export button and show export progress text."""
        if exporting:
            self.exportButton.enabled = False
            self.exportButton.setText("Exporting...")
            slicer.app.processEvents()
        else:
            self.exportButton.setText("Export CSV")
            self.updateSegmentCountAndButtons()

    def _writeMeasurementsCsv(self, filePath, segmentationNode):
        """Write the measurement CSV using the requested count and per-segment rows."""
        rows = self.logic.mucusPlugMeasurementRows(segmentationNode, self.sourceVolumeNode())
        with open(filePath, "w", newline="") as csvFile:
            writer = csv.writer(csvFile)
            writer.writerow(["Mucus plug count", len(rows)])
            writer.writerow([])
            writer.writerow(["Segment", "Volume", "Length"])
            for row in rows:
                writer.writerow([row["segmentName"], row["volumePixels"], row["lengthPixels"]])

    def jumpToSegment(self, segmentID):
        """Center slice views on a segment and apply the current jump zoom."""
        segmentationNode = self.segmentationNode()
        if not self.logic.isValidSegmentID(segmentationNode, segmentID):
            return
        self.logic.ensureSourceVolumeVisible(self.sourceVolumeNode())
        self.logic.ensureSegmentationVisible(segmentationNode)
        self.logic.jumpToSegment(
            segmentationNode,
            segmentID,
            self.zoomSpinBox.value,
            self._sliceBaseFieldOfViewByID,
        )

    def segmentationNode(self):
        """Return the selected mucus segmentation node."""
        return self.segmentationSelector.currentNode()

    def sourceVolumeNode(self):
        """Return the selected CT source volume node."""
        return self.sourceVolumeSelector.currentNode()

    def currentSegmentID(self):
        """Return the active Segment Editor segment ID."""
        if not self.segmentEditorWidget:
            return ""
        return self.segmentEditorWidget.currentSegmentID()

    def updateSegmentCountAndButtons(self):
        """Refresh count text and enable or disable buttons based on current selection."""
        segmentationNode = self.segmentationNode()
        count = self.logic.segmentCount(segmentationNode)
        self.countLabel.setText("Mucus plug count: {}".format(count))

        hasSegments = count > 0
        hasCurrentSegment = self.logic.isValidSegmentID(segmentationNode, self.currentSegmentID())
        hasSegmentation = segmentationNode is not None

        self.addButton.enabled = hasSegmentation
        self.show3DButton.enabled = hasSegmentation
        self.noEditingButton.enabled = hasSegmentation

        self.jumpButton.enabled = hasSegments
        self.lastButton.enabled = hasSegments
        self.nextButton.enabled = hasSegments
        self.exportButton.enabled = hasSegments

        self.deleteButton.enabled = hasCurrentSegment
        self.measureButton.enabled = hasCurrentSegment
        self.paintButton.enabled = hasCurrentSegment
        self.eraseButton.enabled = hasCurrentSegment

    def resetCurrentSegmentMeasurements(self):
        """Clear measurement labels because the displayed values may no longer match the current segment."""
        self.volumeLabel.setText("Volume: not calculated")
        self.lengthLabel.setText("Length: not calculated")

    def _selectFirstSegmentIfNeeded(self):
        """Select the first available segment if the current segment is missing or invalid."""
        segmentationNode = self.segmentationNode()
        if not segmentationNode:
            return
        currentSegmentID = self.currentSegmentID()
        if self.logic.isValidSegmentID(segmentationNode, currentSegmentID):
            return
        segmentIDs = self.logic.segmentIDs(segmentationNode)
        self.segmentEditorWidget.setCurrentSegmentID(segmentIDs[0] if segmentIDs else "")

    def _observeSegmentation(self, segmentationNode):
        """Observe segment changes so count and button state stay current."""
        self._removeSegmentationObservers()
        if not segmentationNode:
            return

        segmentation = segmentationNode.GetSegmentation()
        if not segmentation:
            return

        self._observedSegmentation = segmentation
        events = [vtk.vtkCommand.ModifiedEvent]
        vtkSegmentationClass = getattr(slicer, "vtkSegmentation", None)
        if vtkSegmentationClass:
            for eventName in ("SegmentAdded", "SegmentRemoved", "SegmentModified", "SegmentsOrderModified"):
                if hasattr(vtkSegmentationClass, eventName):
                    events.append(getattr(vtkSegmentationClass, eventName))

        for event in sorted(set(events)):
            tag = segmentation.AddObserver(event, self.onObservedSegmentationChanged)
            self._segmentationObserverTags.append(tag)

    def _removeSegmentationObservers(self):
        """Remove VTK observers from the previously selected segmentation."""
        if self._observedSegmentation:
            for tag in self._segmentationObserverTags:
                self._observedSegmentation.RemoveObserver(tag)
        self._observedSegmentation = None
        self._segmentationObserverTags = []


#
# MucusPlugNavigatorLogic
#


class MucusPlugNavigatorLogic(ScriptedLoadableModuleLogic):
    """Keep non-UI calculations and MRML operations separate from the widget."""

    def segmentIDs(self, segmentationNode):
        """Return segment IDs in the same order used by the segmentation node."""
        if not segmentationNode:
            return []
        segmentation = segmentationNode.GetSegmentation()
        if not segmentation:
            return []
        return [segmentation.GetNthSegmentID(index) for index in range(segmentation.GetNumberOfSegments())]

    def segmentCount(self, segmentationNode):
        """Return the number of mucus plug segments in the selected segmentation."""
        return len(self.segmentIDs(segmentationNode))

    def mucusPlugMeasurementRows(self, segmentationNode, referenceVolumeNode=None):
        """Return CSV-ready measurement rows for every segment in segmentation order."""
        rows = []
        if not segmentationNode:
            return rows

        segmentation = segmentationNode.GetSegmentation()
        for segmentID in self.segmentIDs(segmentationNode):
            segment = segmentation.GetSegment(segmentID) if segmentation else None
            segmentName = segment.GetName() if segment else segmentID
            metrics = self.segmentVoxelMetrics(segmentationNode, segmentID, referenceVolumeNode)
            rows.append(
                {
                    "segmentID": segmentID,
                    "segmentName": segmentName,
                    "volumePixels": metrics["volumePixels"] if metrics else "",
                    "lengthPixels": metrics["lengthPixels"] if metrics else "",
                }
            )
        return rows

    def segmentVoxelMetrics(self, segmentationNode, segmentID, referenceVolumeNode=None):
        """Calculate voxel count and main-axis pixel length for one segment."""
        if not self.isValidSegmentID(segmentationNode, segmentID):
            return None

        try:
            import numpy as np

            occupiedVoxelCoordinates = self._occupiedVoxelCoordinates(
                segmentationNode,
                segmentID,
                referenceVolumeNode,
                np,
            )
        except Exception:
            logging.exception("Could not compute segment voxel measurements for segment: %s", segmentID)
            return None

        volumePixels = int(occupiedVoxelCoordinates.shape[0])
        if volumePixels == 0:
            return {"volumePixels": 0, "lengthPixels": 0}
        if volumePixels == 1:
            return {"volumePixels": volumePixels, "lengthPixels": 1}

        lengthPixels = self._principalAxisLengthPixels(occupiedVoxelCoordinates, np)
        return {"volumePixels": volumePixels, "lengthPixels": max(lengthPixels, 1)}

    def _occupiedVoxelCoordinates(self, segmentationNode, segmentID, referenceVolumeNode, np):
        """Convert a segment labelmap into nonzero voxel coordinates."""
        try:
            segmentArray = slicer.util.arrayFromSegmentBinaryLabelmap(segmentationNode, segmentID)
        except Exception:
            if not referenceVolumeNode:
                raise
            segmentArray = slicer.util.arrayFromSegmentBinaryLabelmap(
                segmentationNode,
                segmentID,
                referenceVolumeNode,
            )
        return np.argwhere(segmentArray != 0)

    def _principalAxisLengthPixels(self, occupiedVoxelCoordinates, np):
        """Estimate segment length in pixels along the principal component axis."""
        centeredCoordinates = occupiedVoxelCoordinates - occupiedVoxelCoordinates.mean(axis=0)
        try:
            _, _, principalAxes = np.linalg.svd(centeredCoordinates, full_matrices=False)
            principalAxis = principalAxes[0]
            projectedCoordinates = occupiedVoxelCoordinates.dot(principalAxis)
            return int(round(projectedCoordinates.max() - projectedCoordinates.min() + 1))
        except Exception:
            voxelDimensions = occupiedVoxelCoordinates.max(axis=0) - occupiedVoxelCoordinates.min(axis=0) + 1
            return int(voxelDimensions.max())

    def isValidSegmentID(self, segmentationNode, segmentID):
        """Return True if the segment ID exists in the selected segmentation."""
        if not segmentationNode or not segmentID:
            return False
        return segmentID in self.segmentIDs(segmentationNode)

    def nextSegmentID(self, segmentationNode, currentSegmentID, wrap=True):
        """Return the next segment ID in segmentation order."""
        segmentIDs = self.segmentIDs(segmentationNode)
        if not segmentIDs:
            return ""
        if currentSegmentID not in segmentIDs:
            return segmentIDs[0]
        currentIndex = segmentIDs.index(currentSegmentID)
        nextIndex = currentIndex + 1
        if nextIndex >= len(segmentIDs):
            nextIndex = 0 if wrap else currentIndex
        return segmentIDs[nextIndex]

    def previousSegmentID(self, segmentationNode, currentSegmentID, wrap=True):
        """Return the previous segment ID in segmentation order."""
        segmentIDs = self.segmentIDs(segmentationNode)
        if not segmentIDs:
            return ""
        if currentSegmentID not in segmentIDs:
            return segmentIDs[-1]
        currentIndex = segmentIDs.index(currentSegmentID)
        previousIndex = currentIndex - 1
        if previousIndex < 0:
            previousIndex = len(segmentIDs) - 1 if wrap else currentIndex
        return segmentIDs[previousIndex]

    def deleteSegmentAndGetNearby(self, segmentationNode, segmentID):
        """Delete a segment and return the closest remaining segment ID."""
        segmentIDs = self.segmentIDs(segmentationNode)
        if segmentID not in segmentIDs:
            return ""

        removedIndex = segmentIDs.index(segmentID)
        segmentation = segmentationNode.GetSegmentation()
        segmentation.RemoveSegment(segmentID)
        segmentationNode.Modified()

        remainingSegmentIDs = self.segmentIDs(segmentationNode)
        if not remainingSegmentIDs:
            return ""
        return remainingSegmentIDs[min(removedIndex, len(remainingSegmentIDs) - 1)]

    def jumpToSegment(self, segmentationNode, segmentID, zoomFactor, baseFieldOfViewBySliceNodeID):
        """Center slice views on a segment and apply a zoom factor."""
        centerRAS = self.segmentCenterRAS(segmentationNode, segmentID)
        if centerRAS is None:
            logging.warning("Could not find center for segment: %s", segmentID)
            return

        self.resetSliceFieldOfViewBaseline(baseFieldOfViewBySliceNodeID)
        for sliceNode in slicer.util.getNodesByClass("vtkMRMLSliceNode"):
            sliceNode.JumpSliceByCentering(centerRAS[0], centerRAS[1], centerRAS[2])

        self.applySliceZoom(zoomFactor, baseFieldOfViewBySliceNodeID)

    def segmentCenterRAS(self, segmentationNode, segmentID):
        """Return the RAS center point for a segment."""
        if not self.isValidSegmentID(segmentationNode, segmentID):
            return None
        try:
            centerRAS = segmentationNode.GetSegmentCenterRAS(segmentID)
        except Exception:
            logging.exception("GetSegmentCenterRAS failed")
            return None
        if centerRAS is None or len(centerRAS) < 3:
            return None
        return [float(centerRAS[0]), float(centerRAS[1]), float(centerRAS[2])]

    def applySliceZoom(self, zoomFactor, baseFieldOfViewBySliceNodeID):
        """Apply the zoom factor by reducing each slice view field-of-view."""
        zoomFactor = max(1.0, float(zoomFactor))
        for sliceNode in slicer.util.getNodesByClass("vtkMRMLSliceNode"):
            sliceNodeID = sliceNode.GetID()
            baseFieldOfViewBySliceNodeID[sliceNodeID] = list(sliceNode.GetFieldOfView())

            baseFieldOfView = baseFieldOfViewBySliceNodeID[sliceNodeID]
            newFieldOfView = [
                max(baseFieldOfView[0] / zoomFactor, 1.0),
                max(baseFieldOfView[1] / zoomFactor, 1.0),
                baseFieldOfView[2],
            ]
            sliceNode.SetFieldOfView(newFieldOfView[0], newFieldOfView[1], newFieldOfView[2])
            sliceNode.UpdateMatrices()

    def resetSliceFieldOfViewBaseline(self, baseFieldOfViewBySliceNodeID):
        """Reset slice views to a fitted baseline before applying jump zoom."""
        baseFieldOfViewBySliceNodeID.clear()
        layoutManager = slicer.app.layoutManager()
        resetAllViewsUsed = False
        for sliceNode in slicer.util.getNodesByClass("vtkMRMLSliceNode"):
            try:
                sliceWidget = layoutManager.sliceWidget(sliceNode.GetLayoutName())
                sliceLogic = sliceWidget.sliceLogic() if sliceWidget else None
                if sliceLogic and hasattr(sliceLogic, "FitSliceToAll"):
                    sliceLogic.FitSliceToAll()
                elif sliceLogic and hasattr(sliceLogic, "FitSliceToBackground"):
                    sliceLogic.FitSliceToBackground()
                elif not resetAllViewsUsed:
                    slicer.util.resetSliceViews()
                    resetAllViewsUsed = True
            except Exception:
                if not resetAllViewsUsed:
                    try:
                        slicer.util.resetSliceViews()
                        resetAllViewsUsed = True
                    except Exception:
                        logging.debug("Could not reset slice field of view before applying jump zoom", exc_info=True)

        for sliceNode in slicer.util.getNodesByClass("vtkMRMLSliceNode"):
            baseFieldOfViewBySliceNodeID[sliceNode.GetID()] = list(sliceNode.GetFieldOfView())

    def ensureSourceVolumeVisible(self, sourceVolumeNode):
        """Set the selected source volume as the slice-view background."""
        if not sourceVolumeNode:
            return
        try:
            slicer.util.setSliceViewerLayers(background=sourceVolumeNode, fit=False)
            return
        except Exception:
            logging.debug("setSliceViewerLayers failed; falling back to slice composite nodes", exc_info=True)

        for compositeNode in slicer.util.getNodesByClass("vtkMRMLSliceCompositeNode"):
            compositeNode.SetBackgroundVolumeID(sourceVolumeNode.GetID())

    def ensureSegmentationVisible(self, segmentationNode):
        """Ensure the segmentation overlay is visible in 2D and globally visible."""
        if not segmentationNode:
            return
        segmentationNode.CreateDefaultDisplayNodes()
        displayNode = segmentationNode.GetDisplayNode()
        if not displayNode:
            return
        displayNode.SetVisibility(True)
        if hasattr(displayNode, "SetVisibility2D"):
            displayNode.SetVisibility2D(True)


#
# MucusPlugNavigatorTest
#


class MucusPlugNavigatorTest(ScriptedLoadableModuleTest):
    """Run minimal logic smoke tests for Slicer's reload-and-test workflow."""

    def setUp(self):
        """Clear the scene before each test."""
        slicer.mrmlScene.Clear()

    def runTest(self):
        """Run all module tests."""
        self.setUp()
        self.test_MucusPlugNavigatorLogic()

    def test_MucusPlugNavigatorLogic(self):
        """Verify that logic helper methods handle empty inputs."""
        logic = MucusPlugNavigatorLogic()
        self.assertEqual(logic.segmentCount(None), 0)
        self.assertEqual(logic.nextSegmentID(None, ""), "")
        self.assertEqual(logic.previousSegmentID(None, ""), "")

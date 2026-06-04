import csv
import json
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
MODULE_NAME = "MucusPlugNavigator"
MODULE_TITLE = "Mucus Plug Navigator"
LOGICALLY_DELETED_SEGMENTS_ATTRIBUTE = "MucusPlugNavigator.LogicallyDeletedSegmentIDs"
DELETED_BACKUP_NODE_ATTRIBUTE = "MucusPlugNavigator.DeletedBackupNodeID"
DELETED_BACKUP_SOURCE_ATTRIBUTE = "MucusPlugNavigator.DeletedBackupSourceNodeID"
DELETED_SEGMENT_COLOR_ATTRIBUTE_PREFIX = "MucusPlugNavigator.DeletedSegmentColor."

BUTTON_MINIMUM_WIDTH = 92
BUTTON_MINIMUM_HEIGHT = 32

JUMP_ZOOM_MINIMUM = 1.0
JUMP_ZOOM_MAXIMUM = 10.0
JUMP_ZOOM_DECIMALS = 1
JUMP_ZOOM_STEP = 0.5
JUMP_ZOOM_DEFAULT = 1.0


EXPORT_MASK_MIN_VOLUME_PIXELS = 100000

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
        self.visibilityShortcut = None
        self.lastShortcut = None
        self.nextShortcut = None

        self._moduleIsActive = False
        self._observedSegmentation = None
        self._segmentationObserverTags = []
        self._observedDisplayNode = None
        self._displayNodeObserverTags = []
        self._sliceBaseFieldOfViewByID = {}
        self._segmentEditorAddButton = None
        self._segmentEditorShow3DButton = None
        self._suppressAutoJump = False

        self._initializeWidgetReferences()

    def setup(self):
        """Build the full module UI and connect it to the current Slicer scene."""
        ScriptedLoadableModuleWidget.setup(self)

        self.segmentEditorNode = self._getOrCreateSegmentEditorNode()

        self._buildSelectorsSection()
        self._buildNavigationSection()
        self._buildActionToolbarSection()
        self._buildEmbeddedSegmentEditorSection()
        self._createVisibilityShortcut()
        self._createNavigationShortcuts()
        self._connectSignals()
        self._initializeWidgetState()

    def cleanup(self):
        """Release observers and Segment Editor view hooks when Slicer unloads the module."""
        self._removeVisibilityShortcut()
        self._removeNavigationShortcuts()
        self._removeSegmentationObservers()
        self._removeSegmentationDisplayObservers()
        if self.segmentEditorWidget:
            self.segmentEditorWidget.setActiveEffect(None)
            self.segmentEditorWidget.removeViewObservations()
            self.segmentEditorWidget.uninstallKeyboardShortcuts()
            self.segmentEditorWidget = None

    def enter(self):
        """Install Segment Editor view hooks when the module becomes active."""
        self._moduleIsActive = True
        if self.visibilityShortcut:
            self.visibilityShortcut.enabled = True
        self._setNavigationShortcutsEnabled(True)
        if self.segmentEditorWidget:
            self.segmentEditorWidget.setMRMLScene(slicer.mrmlScene)
            self.segmentEditorWidget.setupViewObservations()
            self.segmentEditorWidget.installKeyboardShortcuts()
            self.updateSegmentCountAndButtons()

    def exit(self):
        """Remove Segment Editor view hooks when the user leaves the module."""
        self._moduleIsActive = False
        if self.visibilityShortcut:
            self.visibilityShortcut.enabled = False
        self._setNavigationShortcutsEnabled(False)
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
        self.visibilityButton = None
        self.deleteButton = None
        self.restoreButton = None
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
        self._hideBackupJumpButton()

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

        self.visibilityButton = self._createButton("Hide Seg", "Toggle whole segmentation visibility in 2D and 3D. Shortcut: H.")
        segmentToolbarLayout.addWidget(self.visibilityButton, 0, 2)

        self.deleteButton = self._createButton("Delete", "Delete only the currently selected mucus plug segment.")
        segmentToolbarLayout.addWidget(self.deleteButton, 0, 3)

        self.measureButton = self._createButton("Measure", "Calculate volume and length for the currently selected mucus plug.")
        segmentToolbarLayout.addWidget(self.measureButton, 0, 4)

        self.noEditingButton = self._createButton("No editing", "Turn off the active Segment Editor effect.")
        segmentToolbarLayout.addWidget(self.noEditingButton, 1, 0)

        self.paintButton = self._createButton("Paint", "Activate the Segment Editor Paint effect.")
        segmentToolbarLayout.addWidget(self.paintButton, 1, 1)

        self.eraseButton = self._createButton("Erase", "Activate the Segment Editor Erase effect.")
        segmentToolbarLayout.addWidget(self.eraseButton, 1, 2)

        self.exportButton = self._createButton("Export", "Export segment name, volume, and length for all mucus plugs to a CSV file.")
        segmentToolbarLayout.addWidget(self.exportButton, 1, 3)

        self.restoreButton = self._createButton("Restore", "Choose logically deleted mucus plug segments to show again.")
        segmentToolbarLayout.addWidget(self.restoreButton, 1, 4)

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
        self.visibilityButton.connect("clicked(bool)", self.onSegmentationVisibilityButton)
        self.deleteButton.connect("clicked(bool)", self.onDeleteButton)
        self.restoreButton.connect("clicked(bool)", self.onRestoreButton)
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

    def _createVisibilityShortcut(self):
        """Bind the H key to the whole-segmentation visibility button."""
        shortcutParent = slicer.util.mainWindow() if slicer.util.mainWindow() else self.parent
        self.visibilityShortcut = qt.QShortcut(shortcutParent)
        self.visibilityShortcut.setKey(self._visibilityShortcutKeySequence())
        self.visibilityShortcut.setContext(qt.Qt.ApplicationShortcut)
        if hasattr(self.visibilityShortcut, "setAutoRepeat"):
            self.visibilityShortcut.setAutoRepeat(False)
        self.visibilityShortcut.connect("activated()", self.onSegmentationVisibilityShortcut)
        self.visibilityShortcut.connect("activatedAmbiguously()", self.onSegmentationVisibilityShortcut)

    def _createNavigationShortcuts(self):
        """Bind Left and Right Arrow keys to the Last and Next navigation buttons."""
        shortcutParent = slicer.util.mainWindow() if slicer.util.mainWindow() else self.parent
        self.lastShortcut = self._createShortcut(shortcutParent, qt.Qt.Key_Left, "Left", self.onLastShortcut)
        self.nextShortcut = self._createShortcut(shortcutParent, qt.Qt.Key_Right, "Right", self.onNextShortcut)

    def _createShortcut(self, parent, key, fallbackText, callback):
        """Create an application-level shortcut for one keyboard action."""
        shortcut = qt.QShortcut(parent)
        shortcut.setKey(self._keySequence(key, fallbackText))
        shortcut.setContext(qt.Qt.ApplicationShortcut)
        if hasattr(shortcut, "setAutoRepeat"):
            shortcut.setAutoRepeat(False)
        shortcut.connect("activated()", callback)
        shortcut.connect("activatedAmbiguously()", callback)
        return shortcut

    def _setNavigationShortcutsEnabled(self, enabled):
        """Enable or disable arrow-key navigation shortcuts together."""
        for shortcut in (self.lastShortcut, self.nextShortcut):
            if shortcut:
                shortcut.enabled = enabled

    def _removeNavigationShortcuts(self):
        """Disable and delete arrow-key navigation shortcuts during cleanup."""
        for shortcut in (self.lastShortcut, self.nextShortcut):
            if not shortcut:
                continue
            shortcut.enabled = False
            try:
                shortcut.deleteLater()
            except Exception:
                logging.debug("Could not delete navigation shortcut cleanly", exc_info=True)
        self.lastShortcut = None
        self.nextShortcut = None

    def _visibilityShortcutKeySequence(self):
        """Return an H key sequence that works across different Qt builds."""
        return self._keySequence(qt.Qt.Key_H, "H")

    def _keySequence(self, key, fallbackText=None):
        """Return a key sequence from a Qt key code, with an optional text fallback."""
        try:
            return qt.QKeySequence(key)
        except Exception:
            return qt.QKeySequence(fallbackText if fallbackText else "")

    def _removeVisibilityShortcut(self):
        """Disable and delete the H shortcut so reloads do not leave duplicate bindings."""
        if not self.visibilityShortcut:
            return
        self.visibilityShortcut.enabled = False
        try:
            self.visibilityShortcut.deleteLater()
        except Exception:
            logging.debug("Could not delete visibility shortcut cleanly", exc_info=True)
        self.visibilityShortcut = None

    def _visibilityShortcutBlockingReason(self):
        """Return a short user-facing reason when H cannot toggle visibility."""
        if not self._isModuleActiveForShortcut():
            return "H shortcut ignored because Mucus Plug Navigator is not active."
        if self._focusWidgetAcceptsTypedShortcut():
            return "H shortcut ignored because a text field is active."
        if not self.segmentationNode():
            return "H shortcut cannot hide/show segmentation: no segmentation is selected."
        if not self.visibilityButton:
            return "H shortcut cannot hide/show segmentation: visibility button is not ready."
        if not self.visibilityButton.enabled:
            return "H shortcut cannot hide/show segmentation: visibility button is disabled."
        return ""

    def _navigationShortcutBlockingReason(self):
        """Return a short user-facing reason when arrow keys cannot navigate mucus plugs."""
        if not self._isModuleActiveForShortcut():
            return "Arrow shortcut ignored because Mucus Plug Navigator is not active."
        if self._focusWidgetAcceptsTypedShortcut():
            return "Arrow shortcut ignored because a text field is active."
        if not self.segmentationNode():
            return "Arrow shortcut cannot navigate: no segmentation is selected."
        if self.logic.activeSegmentCount(self.segmentationNode()) == 0:
            return "Arrow shortcut cannot navigate: no mucus plug segments are available."
        return ""


    def _isModuleActiveForShortcut(self):
        """Return True when this module should respond to the visibility shortcut."""
        if self._moduleIsActive:
            return True
        try:
            if self.parent and self.parent.isVisible():
                return True
        except Exception:
            pass
        try:
            selectedModule = slicer.util.selectedModule()
            return selectedModule in (MODULE_NAME, MODULE_TITLE)
        except Exception:
            return False

    def _focusWidgetAcceptsTypedShortcut(self):
        """Return True when H should be left for a focused text-editing widget."""
        try:
            focusWidget = qt.QApplication.focusWidget()
        except Exception:
            return False
        if not focusWidget:
            return False
        try:
            className = str(focusWidget.metaObject().className())
        except Exception:
            className = focusWidget.__class__.__name__
        return className in ("QLineEdit", "QTextEdit", "QPlainTextEdit")

    def _showShortcutMessage(self, message):
        """Show shortcut diagnostics in Slicer's status bar, with logging as fallback."""
        try:
            slicer.util.showStatusMessage(message, 3000)
            return
        except Exception:
            pass
        try:
            mainWindow = slicer.util.mainWindow()
            if mainWindow and mainWindow.statusBar():
                mainWindow.statusBar().showMessage(message, 3000)
                return
        except Exception:
            pass
        logging.warning(message)

    def _hideBackupJumpButton(self):
        """Keep the manual Jump button in code as a backup, but hide it from the normal UI."""
        if self.jumpButton:
            self.jumpButton.hide()

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
        self._copyButtonIconSize(show3DButton if show3DButton else addButton, self.visibilityButton)
        self._copyButtonIconSize(removeButton if removeButton else addButton, self.deleteButton)
        self._copyButtonIconSize(addButton, self.restoreButton)
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
            self.visibilityButton,
            self.deleteButton,
            self.restoreButton,
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
        """Clear stale measurements, refresh buttons, and auto-jump after user segment clicks."""
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()
        if not self._suppressAutoJump and self.logic.isActiveSegmentID(self.segmentationNode(), segmentID):
            self.jumpToSegment(segmentID)

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
        if not self.logic.isActiveSegmentID(self.segmentationNode(), segmentID):
            self._selectFirstSegmentIfNeeded()
            segmentID = self.currentSegmentID()
        self.jumpToSegment(segmentID)

    def onLastButton(self, checked=False):
        """Select the previous segment in segmentation order and jump to it."""
        segmentationNode = self.segmentationNode()
        previousSegmentID = self.logic.previousSegmentID(segmentationNode, self.currentSegmentID(), wrap=True)
        if not previousSegmentID:
            return
        self._setCurrentSegmentIDWithoutAutoJump(previousSegmentID)
        self.jumpToSegment(previousSegmentID)
        self.updateSegmentCountAndButtons()

    def onNextButton(self, checked=False):
        """Select the next segment in segmentation order and jump to it."""
        segmentationNode = self.segmentationNode()
        nextSegmentID = self.logic.nextSegmentID(segmentationNode, self.currentSegmentID(), wrap=True)
        if not nextSegmentID:
            return
        self._setCurrentSegmentIDWithoutAutoJump(nextSegmentID)
        self.jumpToSegment(nextSegmentID)
        self.updateSegmentCountAndButtons()

    def onLastShortcut(self):
        """Run Last from the Left Arrow shortcut after checking keyboard navigation state."""
        self._runNavigationShortcut(self.onLastButton)

    def onNextShortcut(self):
        """Run Next from the Right Arrow shortcut after checking keyboard navigation state."""
        self._runNavigationShortcut(self.onNextButton)

    def _runNavigationShortcut(self, navigationCallback):
        """Run a keyboard navigation callback or show why it cannot run."""
        blockingReason = self._navigationShortcutBlockingReason()
        if blockingReason:
            self._showShortcutMessage(blockingReason)
            return
        navigationCallback()

    def onAddButton(self, checked=False):
        """Add a segment using Segment Editor behavior, with a direct fallback."""
        if self._segmentEditorAddButton:
            self._runWithoutAutoJump(self._segmentEditorAddButton.click)
        else:
            segmentationNode = self.segmentationNode()
            if segmentationNode:
                segmentID = segmentationNode.GetSegmentation().AddEmptySegment()
                self._setCurrentSegmentIDWithoutAutoJump(segmentID)
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

    def onSegmentationVisibilityButton(self, checked=False):
        """Toggle the selected segmentation visibility in both 2D and 3D views."""
        segmentationNode = self.segmentationNode()
        if not segmentationNode:
            return None
        visible = not self.logic.isSegmentationVisible(segmentationNode)
        self.logic.setSegmentationVisible(segmentationNode, visible)
        self.updateSegmentCountAndButtons()
        return visible

    def onSegmentationVisibilityShortcut(self):
        """Toggle whole-segmentation visibility when the user presses H."""
        blockingReason = self._visibilityShortcutBlockingReason()
        if blockingReason:
            self._showShortcutMessage(blockingReason)
            return
        visible = self.onSegmentationVisibilityButton()
        self._showShortcutMessage("Mucus segmentation is now {}.".format("visible" if visible else "hidden"))

    def onDeleteButton(self, checked=False):
        """Logically delete the current segment by moving it to the hidden restore backup."""
        segmentationNode = self.segmentationNode()
        segmentID = self.currentSegmentID()
        if not self.logic.isValidSegmentID(segmentationNode, segmentID):
            return
        if self.logic.isLogicallyDeletedSegment(segmentationNode, segmentID):
            self._showShortcutMessage("This mucus plug segment is already logically deleted.")
            return

        segment = segmentationNode.GetSegmentation().GetSegment(segmentID)
        segmentName = segment.GetName() if segment else segmentID
        answer = qt.QMessageBox.question(
            slicer.util.mainWindow(),
            "Delete mucus plug segment",
            "Move this mucus plug segment to the deleted list?\nIt will disappear from the segment table, but you can restore it later.\n\n{}".format(segmentName),
            qt.QMessageBox.Yes | qt.QMessageBox.No,
            qt.QMessageBox.No,
        )
        if answer != qt.QMessageBox.Yes:
            return

        nextSegmentID = self.logic.logicalDeleteSegmentAndGetNearby(segmentationNode, segmentID)
        self._setCurrentSegmentIDWithoutAutoJump(nextSegmentID if nextSegmentID else "")
        self.updateSegmentCountAndButtons()
        if nextSegmentID:
            self.jumpToSegment(nextSegmentID)

    def onRestoreButton(self, checked=False):
        """Show a chooser for logically deleted segments and restore the selected ones."""
        segmentationNode = self.segmentationNode()
        deletedSegmentIDs = self.logic.logicallyDeletedSegmentIDs(segmentationNode)
        if not deletedSegmentIDs:
            self._showShortcutMessage("No logically deleted mucus plug segments to restore.")
            return

        selectedSegmentIDs = self._promptForDeletedSegmentsToRestore(segmentationNode, deletedSegmentIDs)
        if selectedSegmentIDs is None:
            return
        if not selectedSegmentIDs:
            self._showShortcutMessage("No mucus plug segments were selected to restore.")
            return

        restoredSegmentIDs = self.logic.restoreLogicallyDeletedSegments(segmentationNode, selectedSegmentIDs)
        self.updateSegmentCountAndButtons()
        if not restoredSegmentIDs:
            self._showShortcutMessage("No selected mucus plug segments could be restored.")
            return

        firstRestoredSegmentID = restoredSegmentIDs[0]
        self._setCurrentSegmentIDWithoutAutoJump(firstRestoredSegmentID)
        self.jumpToSegment(firstRestoredSegmentID)
        self._showShortcutMessage("Restored {} logically deleted mucus plug segment(s).".format(len(restoredSegmentIDs)))

    def _promptForDeletedSegmentsToRestore(self, segmentationNode, deletedSegmentIDs):
        """Ask the user which logically deleted segment IDs should be restored."""
        dialog = qt.QDialog(slicer.util.mainWindow())
        dialog.setWindowTitle("Restore mucus plug segments")

        layout = qt.QVBoxLayout(dialog)
        layout.addWidget(qt.QLabel("Select mucus plug segment(s) to restore:"))

        listWidget = qt.QListWidget()
        listWidget.setSelectionMode(qt.QAbstractItemView.ExtendedSelection)
        backupNode = self.logic.deletedBackupNode(segmentationNode, create=False)
        segmentation = backupNode.GetSegmentation() if backupNode else None
        for index, segmentID in enumerate(deletedSegmentIDs):
            segment = segmentation.GetSegment(segmentID) if segmentation else None
            segmentName = segment.GetName() if segment else segmentID
            item = qt.QListWidgetItem(segmentName)
            item.setIcon(self._segmentColorIcon(segment, backupNode, segmentID))
            item.setData(qt.Qt.UserRole, segmentID)
            listWidget.addItem(item)
            if index == 0:
                item.setSelected(True)
        layout.addWidget(listWidget)

        buttonLayout = qt.QHBoxLayout()
        buttonLayout.addStretch(1)
        restoreButton = qt.QPushButton("Restore selected")
        cancelButton = qt.QPushButton("Cancel")
        restoreButton.connect("clicked(bool)", lambda checked=False: dialog.accept())
        cancelButton.connect("clicked(bool)", lambda checked=False: dialog.reject())
        buttonLayout.addWidget(restoreButton)
        buttonLayout.addWidget(cancelButton)
        layout.addLayout(buttonLayout)

        if dialog.exec_() != qt.QDialog.Accepted:
            return None
        return [str(item.data(qt.Qt.UserRole)) for item in listWidget.selectedItems()]

    def _segmentColorIcon(self, segment, backupNode=None, segmentID=""):
        """Create a small color-square icon for a deleted segment list item."""
        color = self.logic.deletedSegmentColor(backupNode, segmentID, segment)

        pixmap = qt.QPixmap(18, 18)
        pixmap.fill(qt.QColor(int(color[0] * 255), int(color[1] * 255), int(color[2] * 255)))
        return qt.QIcon(pixmap)

    def onMeasureButton(self, checked=False):
        """Calculate volume and length for the current segment only when requested."""
        segmentationNode = self.segmentationNode()
        segmentID = self.currentSegmentID()
        if not self.logic.isActiveSegmentID(segmentationNode, segmentID):
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
        segmentIDs = self.logic.activeSegmentIDs(segmentationNode)
        if not segmentIDs:
            slicer.util.warningDisplay("No mucus plug segments to export.")
            return

        filePath = self._promptForExportPath(segmentationNode.GetName())
        if not filePath:
            return

        self._setExportInProgress(True)
        try:
            exportedCount, skippedCount = self._writeMeasurementsCsv(filePath, segmentationNode)
            message = "Exported {} mucus plug measurements to:\n{}".format(exportedCount, filePath)
            if skippedCount:
                message += "\n\nSkipped {} large mask-like segment(s).".format(skippedCount)
            slicer.util.infoDisplay(message)
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
            self.exportButton.setText("Export")
            self.updateSegmentCountAndButtons()

    def _writeMeasurementsCsv(self, filePath, segmentationNode):
        """Write the measurement CSV using the requested count and per-segment rows."""
        rows, skippedRows = self.logic.exportMucusPlugMeasurementRows(segmentationNode, self.sourceVolumeNode())
        with open(filePath, "w", newline="") as csvFile:
            writer = csv.writer(csvFile)
            writer.writerow(["Mucus plug count", len(rows)])
            writer.writerow([])
            writer.writerow(["Segment", "Volume", "Length"])
            for row in rows:
                writer.writerow([row["segmentName"], row["volumePixels"], row["lengthPixels"]])
        return len(rows), len(skippedRows)

    def jumpToSegment(self, segmentID):
        """Center slice views on a segment and apply the current jump zoom."""
        segmentationNode = self.segmentationNode()
        if not self.logic.isActiveSegmentID(segmentationNode, segmentID):
            return
        self.logic.ensureSourceVolumeVisible(self.sourceVolumeNode())
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
        count = self.logic.activeSegmentCount(segmentationNode)
        deletedCount = self.logic.logicallyDeletedSegmentCount(segmentationNode)
        self.countLabel.setText("Mucus plug count: {}".format(count))

        hasSegments = count > 0
        hasCurrentSegment = self.logic.isActiveSegmentID(segmentationNode, self.currentSegmentID())
        hasSegmentation = segmentationNode is not None

        self.addButton.enabled = hasSegmentation
        self.show3DButton.enabled = hasSegmentation
        self.visibilityButton.enabled = hasSegmentation
        self.noEditingButton.enabled = hasSegmentation
        self._updateSegmentationVisibilityButtonText()

        self.jumpButton.enabled = hasSegments
        self.lastButton.enabled = hasSegments
        self.nextButton.enabled = hasSegments
        self.exportButton.enabled = hasSegments
        self.restoreButton.enabled = deletedCount > 0

        self.deleteButton.enabled = hasCurrentSegment
        self.measureButton.enabled = hasCurrentSegment
        self.paintButton.enabled = hasCurrentSegment
        self.eraseButton.enabled = hasCurrentSegment

    def resetCurrentSegmentMeasurements(self):
        """Clear measurement labels because the displayed values may no longer match the current segment."""
        self.volumeLabel.setText("Volume: not calculated")
        self.lengthLabel.setText("Length: not calculated")

    def _selectFirstSegmentIfNeeded(self):
        """Select the first active segment if the current segment is missing, invalid, or deleted."""
        segmentationNode = self.segmentationNode()
        if not segmentationNode:
            return
        currentSegmentID = self.currentSegmentID()
        if self.logic.isActiveSegmentID(segmentationNode, currentSegmentID):
            return
        segmentIDs = self.logic.activeSegmentIDs(segmentationNode)
        self._setCurrentSegmentIDWithoutAutoJump(segmentIDs[0] if segmentIDs else "")

    def _setCurrentSegmentIDWithoutAutoJump(self, segmentID):
        """Select a segment from module code without triggering the auto-jump handler."""
        def setCurrentSegment():
            """Set the Segment Editor current segment."""
            self.segmentEditorWidget.setCurrentSegmentID(segmentID)

        self._runWithoutAutoJump(setCurrentSegment)

    def _runWithoutAutoJump(self, callback):
        """Run a small operation while temporarily suppressing selection-change auto-jump."""
        previousSuppressAutoJump = self._suppressAutoJump
        self._suppressAutoJump = True
        try:
            callback()
        finally:
            self._suppressAutoJump = previousSuppressAutoJump

    def _updateSegmentationVisibilityButtonText(self):
        """Update the whole-segmentation visibility button text to match the display state."""
        if not self.visibilityButton:
            return
        segmentationNode = self.segmentationNode()
        if not segmentationNode:
            self.visibilityButton.setText("Hide Seg")
            return
        if self.logic.isSegmentationVisible(segmentationNode):
            self.visibilityButton.setText("Hide Seg")
        else:
            self.visibilityButton.setText("Show Seg")

    def _observeSegmentation(self, segmentationNode):
        """Observe segment changes so count and button state stay current."""
        self._removeSegmentationObservers()
        self._removeSegmentationDisplayObservers()
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

        self._observeSegmentationDisplayNode(segmentationNode)

    def _removeSegmentationObservers(self):
        """Remove VTK observers from the previously selected segmentation."""
        if self._observedSegmentation:
            for tag in self._segmentationObserverTags:
                self._observedSegmentation.RemoveObserver(tag)
        self._observedSegmentation = None
        self._segmentationObserverTags = []

    def _observeSegmentationDisplayNode(self, segmentationNode):
        """Observe display-node visibility changes so the custom visibility button stays current."""
        segmentationNode.CreateDefaultDisplayNodes()
        displayNode = segmentationNode.GetDisplayNode()
        if not displayNode:
            return
        self._observedDisplayNode = displayNode
        tag = displayNode.AddObserver(vtk.vtkCommand.ModifiedEvent, self.onObservedSegmentationDisplayChanged)
        self._displayNodeObserverTags.append(tag)

    def _removeSegmentationDisplayObservers(self):
        """Remove VTK observers from the previously selected segmentation display node."""
        if self._observedDisplayNode:
            for tag in self._displayNodeObserverTags:
                self._observedDisplayNode.RemoveObserver(tag)
        self._observedDisplayNode = None
        self._displayNodeObserverTags = []

    def onObservedSegmentationDisplayChanged(self, caller=None, event=None):
        """Refresh visibility controls when the segmentation display node changes."""
        self.updateSegmentCountAndButtons()


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

    def activeSegmentIDs(self, segmentationNode):
        """Return segment IDs currently present in the active segmentation."""
        self.migrateLegacyLogicalDeletesToBackup(segmentationNode)
        return self.segmentIDs(segmentationNode)

    def activeSegmentCount(self, segmentationNode):
        """Return the number of segments currently treated as active mucus plugs."""
        return len(self.activeSegmentIDs(segmentationNode))

    def logicallyDeletedSegmentIDs(self, segmentationNode):
        """Return segment IDs moved into this module's hidden deleted-segment backup node."""
        if not segmentationNode:
            return []
        self.migrateLegacyLogicalDeletesToBackup(segmentationNode)
        return self.segmentIDs(self.deletedBackupNode(segmentationNode, create=False))

    def logicallyDeletedSegmentCount(self, segmentationNode):
        """Return the number of logically deleted segments still present in the segmentation."""
        return len(self.logicallyDeletedSegmentIDs(segmentationNode))

    def isLogicallyDeletedSegment(self, segmentationNode, segmentID):
        """Return True if a segment has been moved into the hidden deleted-segment backup node."""
        return segmentID in self.logicallyDeletedSegmentIDs(segmentationNode)

    def isActiveSegmentID(self, segmentationNode, segmentID):
        """Return True if a segment exists in the active segmentation."""
        return self.isValidSegmentID(segmentationNode, segmentID)

    def logicalDeleteSegmentAndGetNearby(self, segmentationNode, segmentID):
        """Move a segment into a hidden backup node and return a nearby active segment ID."""
        activeSegmentIDs = self.activeSegmentIDs(segmentationNode)
        if segmentID not in activeSegmentIDs:
            return ""

        removedIndex = activeSegmentIDs.index(segmentID)
        self.moveSegmentToDeletedBackup(segmentationNode, segmentID)

        remainingSegmentIDs = self.activeSegmentIDs(segmentationNode)
        if not remainingSegmentIDs:
            return ""
        return remainingSegmentIDs[min(removedIndex, len(remainingSegmentIDs) - 1)]

    def restoreLogicallyDeletedSegments(self, segmentationNode, segmentIDs=None):
        """Move selected deleted segments from the hidden backup node back to the active segmentation."""
        deletedSegmentIDs = self.logicallyDeletedSegmentIDs(segmentationNode)
        if segmentIDs is None:
            restoredSegmentIDs = deletedSegmentIDs
        else:
            requestedSegmentIDs = set(segmentIDs)
            restoredSegmentIDs = [segmentID for segmentID in deletedSegmentIDs if segmentID in requestedSegmentIDs]
        backupNode = self.deletedBackupNode(segmentationNode, create=False)
        if not backupNode:
            return []
        actuallyRestoredSegmentIDs = []
        for segmentID in restoredSegmentIDs:
            if self.copySegmentBetweenSegmentations(backupNode, segmentationNode, segmentID):
                backupNode.GetSegmentation().RemoveSegment(segmentID)
                backupNode.SetAttribute(self.deletedSegmentColorAttributeName(segmentID), None)
                backupNode.Modified()
                actuallyRestoredSegmentIDs.append(segmentID)
        segmentationNode.Modified()
        self.cleanupDeletedBackupNodeIfEmpty(segmentationNode)
        return actuallyRestoredSegmentIDs

    def setSegmentVisible(self, segmentationNode, segmentID, visible):
        """Set per-segment visibility without changing segment voxel data."""
        if not self.isValidSegmentID(segmentationNode, segmentID):
            return
        segmentationNode.CreateDefaultDisplayNodes()
        displayNode = segmentationNode.GetDisplayNode()
        if not displayNode:
            return
        if hasattr(displayNode, "SetSegmentVisibility"):
            displayNode.SetSegmentVisibility(segmentID, bool(visible))
        displayNode.Modified()

    def moveSegmentToDeletedBackup(self, segmentationNode, segmentID):
        """Copy a segment into the hidden deleted backup node and remove it from the active segmentation."""
        backupNode = self.deletedBackupNode(segmentationNode, create=True)
        if not backupNode:
            return False
        self.storeDeletedSegmentColor(segmentationNode, backupNode, segmentID)
        if not self.copySegmentBetweenSegmentations(segmentationNode, backupNode, segmentID):
            return False
        segmentationNode.GetSegmentation().RemoveSegment(segmentID)
        segmentationNode.Modified()
        return True

    def copySegmentBetweenSegmentations(self, sourceSegmentationNode, targetSegmentationNode, segmentID):
        """Copy one segment between segmentation nodes while preserving its segment ID when possible."""
        if not self.isValidSegmentID(sourceSegmentationNode, segmentID) or not targetSegmentationNode:
            return False
        sourceSegmentation = sourceSegmentationNode.GetSegmentation()
        targetSegmentation = targetSegmentationNode.GetSegmentation()
        if not sourceSegmentation or not targetSegmentation:
            return False
        if targetSegmentation.GetSegment(segmentID):
            return True
        try:
            segmentCopy = slicer.vtkSegment()
            segmentCopy.DeepCopy(sourceSegmentation.GetSegment(segmentID))
            targetSegmentation.AddSegment(segmentCopy, segmentID)
            targetSegmentationNode.Modified()
            return targetSegmentation.GetSegment(segmentID) is not None
        except Exception:
            logging.debug("vtkSegment.DeepCopy failed; trying CopySegmentFromSegmentation", exc_info=True)
        try:
            targetSegmentation.CopySegmentFromSegmentation(sourceSegmentation, segmentID)
            targetSegmentationNode.Modified()
            return targetSegmentation.GetSegment(segmentID) is not None
        except Exception:
            logging.exception("Could not copy segment: %s", segmentID)
            return False

    def deletedBackupNode(self, segmentationNode, create=False):
        """Return the hidden segmentation node used to keep logically deleted segments restorable."""
        if not segmentationNode:
            return None
        backupNode = None
        backupNodeID = segmentationNode.GetAttribute(DELETED_BACKUP_NODE_ATTRIBUTE)
        if backupNodeID:
            backupNode = slicer.mrmlScene.GetNodeByID(backupNodeID)
        if backupNode or not create:
            return backupNode

        backupNodeName = "{} deleted mucus backup".format(segmentationNode.GetName())
        backupNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", backupNodeName)
        backupNode.SetAttribute(DELETED_BACKUP_SOURCE_ATTRIBUTE, segmentationNode.GetID())
        segmentationNode.SetAttribute(DELETED_BACKUP_NODE_ATTRIBUTE, backupNode.GetID())
        self.configureDeletedBackupNode(backupNode)
        return backupNode

    def configureDeletedBackupNode(self, backupNode):
        """Hide the deleted-segment backup node from normal Slicer editors and views."""
        if not backupNode:
            return
        try:
            backupNode.HideFromEditorsOn()
        except Exception:
            try:
                backupNode.SetHideFromEditors(True)
            except Exception:
                logging.debug("Could not hide deleted backup node from editors", exc_info=True)
        backupNode.CreateDefaultDisplayNodes()
        displayNode = backupNode.GetDisplayNode()
        if displayNode:
            displayNode.SetVisibility(False)
            if hasattr(displayNode, "SetVisibility2D"):
                displayNode.SetVisibility2D(False)
            if hasattr(displayNode, "SetVisibility3D"):
                displayNode.SetVisibility3D(False)
        backupNode.Modified()

    def storeDeletedSegmentColor(self, segmentationNode, backupNode, segmentID):
        """Store a deleted segment's color on the backup node so the restore list can show it."""
        segment = segmentationNode.GetSegmentation().GetSegment(segmentID) if segmentationNode else None
        color = self.segmentColor(segment)
        if backupNode and color:
            backupNode.SetAttribute(self.deletedSegmentColorAttributeName(segmentID), json.dumps(color))

    def deletedSegmentColor(self, backupNode, segmentID, segment=None):
        """Return the stored or segment-defined color for a deleted segment."""
        if backupNode and segmentID:
            storedValue = backupNode.GetAttribute(self.deletedSegmentColorAttributeName(segmentID))
            if storedValue:
                try:
                    color = json.loads(storedValue)
                    if len(color) >= 3:
                        return [float(color[0]), float(color[1]), float(color[2])]
                except Exception:
                    logging.debug("Could not parse deleted segment color", exc_info=True)
        color = self.segmentColor(segment)
        return color if color else [0.5, 0.5, 0.5]

    def segmentColor(self, segment):
        """Return a segment color as RGB values between 0 and 1."""
        if not segment:
            return None
        try:
            color = segment.GetColor()
            if color and len(color) >= 3:
                return [float(color[0]), float(color[1]), float(color[2])]
        except Exception:
            pass
        try:
            color = [0.5, 0.5, 0.5]
            segment.GetColor(color)
            return [float(color[0]), float(color[1]), float(color[2])]
        except Exception:
            logging.debug("Could not read segment color", exc_info=True)
            return None

    def deletedSegmentColorAttributeName(self, segmentID):
        """Return the backup-node attribute name used to store one deleted segment color."""
        return "{}{}".format(DELETED_SEGMENT_COLOR_ATTRIBUTE_PREFIX, segmentID)

    def cleanupDeletedBackupNodeIfEmpty(self, segmentationNode):
        """Remove the hidden backup node reference when it has no deleted segments."""
        backupNode = self.deletedBackupNode(segmentationNode, create=False)
        if not backupNode or self.segmentCount(backupNode) > 0:
            return
        segmentationNode.SetAttribute(DELETED_BACKUP_NODE_ATTRIBUTE, None)
        slicer.mrmlScene.RemoveNode(backupNode)
        segmentationNode.Modified()

    def migrateLegacyLogicalDeletesToBackup(self, segmentationNode):
        """Move old hidden-only logical deletes into the hidden backup node so rows disappear."""
        if not segmentationNode:
            return
        legacySegmentIDs = self.legacyLogicallyDeletedSegmentIDs(segmentationNode)
        if not legacySegmentIDs:
            return
        for segmentID in legacySegmentIDs:
            if self.isValidSegmentID(segmentationNode, segmentID):
                self.moveSegmentToDeletedBackup(segmentationNode, segmentID)
        segmentationNode.SetAttribute(LOGICALLY_DELETED_SEGMENTS_ATTRIBUTE, None)
        segmentationNode.Modified()

    def legacyLogicallyDeletedSegmentIDs(self, segmentationNode):
        """Return old hidden-only logical delete IDs stored before the backup-node design."""
        if not segmentationNode:
            return []
        storedValue = segmentationNode.GetAttribute(LOGICALLY_DELETED_SEGMENTS_ATTRIBUTE)
        if not storedValue:
            return []
        try:
            segmentIDs = json.loads(storedValue)
        except Exception:
            logging.debug("Could not parse legacy logical delete segment list", exc_info=True)
            return []
        validSegmentIDs = set(self.segmentIDs(segmentationNode))
        return [segmentID for segmentID in segmentIDs if segmentID in validSegmentIDs]

    def _setLogicallyDeletedSegmentIDs(self, segmentationNode, segmentIDs):
        """Store logical delete segment IDs on the segmentation node."""
        if not segmentationNode:
            return
        orderedUniqueSegmentIDs = []
        validSegmentIDs = set(self.segmentIDs(segmentationNode))
        for segmentID in segmentIDs:
            if segmentID in validSegmentIDs and segmentID not in orderedUniqueSegmentIDs:
                orderedUniqueSegmentIDs.append(segmentID)
        if orderedUniqueSegmentIDs:
            segmentationNode.SetAttribute(LOGICALLY_DELETED_SEGMENTS_ATTRIBUTE, json.dumps(orderedUniqueSegmentIDs))
        else:
            segmentationNode.SetAttribute(LOGICALLY_DELETED_SEGMENTS_ATTRIBUTE, None)
        segmentationNode.Modified()

    def mucusPlugMeasurementRows(self, segmentationNode, referenceVolumeNode=None, skipLengthAbovePixels=None):
        """Return CSV-ready measurement rows for every segment in segmentation order."""
        rows = []
        if not segmentationNode:
            return rows

        segmentation = segmentationNode.GetSegmentation()
        for segmentID in self.activeSegmentIDs(segmentationNode):
            segment = segmentation.GetSegment(segmentID) if segmentation else None
            segmentName = segment.GetName() if segment else segmentID
            metrics = self.segmentVoxelMetrics(
                segmentationNode,
                segmentID,
                referenceVolumeNode,
                skipLengthAbovePixels,
            )
            rows.append(
                {
                    "segmentID": segmentID,
                    "segmentName": segmentName,
                    "volumePixels": metrics["volumePixels"] if metrics else "",
                    "lengthPixels": metrics["lengthPixels"] if metrics else "",
                }
            )
        return rows

    def exportMucusPlugMeasurementRows(self, segmentationNode, referenceVolumeNode=None):
        """Return export rows after removing large mask-like segments from the CSV output."""
        rows = self.mucusPlugMeasurementRows(
            segmentationNode,
            referenceVolumeNode,
            skipLengthAbovePixels=EXPORT_MASK_MIN_VOLUME_PIXELS,
        )
        exportRows = []
        skippedRows = []
        for row in rows:
            if self._isMaskLikeExportRow(row):
                skippedRows.append(row)
            else:
                exportRows.append(row)
        return exportRows, skippedRows

    def _isMaskLikeExportRow(self, row):
        """Return True when an export row looks like a whole-mask segment instead of a mucus plug."""
        try:
            return int(row["volumePixels"]) >= EXPORT_MASK_MIN_VOLUME_PIXELS
        except Exception:
            return False

    def segmentVoxelMetrics(self, segmentationNode, segmentID, referenceVolumeNode=None, skipLengthAbovePixels=None):
        """Calculate voxel count and main-axis pixel length for one segment."""
        if not self.isValidSegmentID(segmentationNode, segmentID):
            return None

        try:
            import numpy as np

            segmentArray = self._segmentArray(
                segmentationNode,
                segmentID,
                referenceVolumeNode,
            )
            occupiedMask = segmentArray != 0
            volumePixels = int(np.count_nonzero(occupiedMask))
        except Exception:
            logging.exception("Could not compute segment voxel measurements for segment: %s", segmentID)
            return None

        if volumePixels == 0:
            return {"volumePixels": 0, "lengthPixels": 0}
        if volumePixels == 1:
            return {"volumePixels": volumePixels, "lengthPixels": 1}
        if skipLengthAbovePixels is not None and volumePixels >= skipLengthAbovePixels:
            return {"volumePixels": volumePixels, "lengthPixels": ""}

        occupiedVoxelCoordinates = np.argwhere(occupiedMask)
        lengthPixels = self._principalAxisLengthPixels(occupiedVoxelCoordinates, np)
        return {"volumePixels": volumePixels, "lengthPixels": max(lengthPixels, 1)}

    def _segmentArray(self, segmentationNode, segmentID, referenceVolumeNode):
        """Return a binary labelmap array for one segment, using the source volume when needed."""
        try:
            return slicer.util.arrayFromSegmentBinaryLabelmap(segmentationNode, segmentID)
        except Exception:
            if not referenceVolumeNode:
                raise
            return slicer.util.arrayFromSegmentBinaryLabelmap(
                segmentationNode,
                segmentID,
                referenceVolumeNode,
            )

    def _occupiedVoxelCoordinates(self, segmentationNode, segmentID, referenceVolumeNode, np):
        """Convert a segment labelmap into nonzero voxel coordinates."""
        segmentArray = self._segmentArray(segmentationNode, segmentID, referenceVolumeNode)
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
        segmentIDs = self.activeSegmentIDs(segmentationNode)
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
        segmentIDs = self.activeSegmentIDs(segmentationNode)
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

    def isSegmentationVisible(self, segmentationNode):
        """Return True when the segmentation display node is globally visible."""
        if not segmentationNode:
            return False
        segmentationNode.CreateDefaultDisplayNodes()
        displayNode = segmentationNode.GetDisplayNode()
        if not displayNode:
            return False
        return bool(displayNode.GetVisibility())

    def setSegmentationVisible(self, segmentationNode, visible):
        """Set whole-segmentation visibility in both 2D and 3D views."""
        if not segmentationNode:
            return
        segmentationNode.CreateDefaultDisplayNodes()
        displayNode = segmentationNode.GetDisplayNode()
        if not displayNode:
            return
        displayNode.SetVisibility(bool(visible))
        if hasattr(displayNode, "SetVisibility2D"):
            displayNode.SetVisibility2D(bool(visible))
        if hasattr(displayNode, "SetVisibility3D"):
            displayNode.SetVisibility3D(bool(visible))
        displayNode.Modified()

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

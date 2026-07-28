import csv
import json
import logging
import os
import re
import subprocess
import sys
import time

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
SEGMENT_NAME_LABEL_NODE_NAME = "MucusPlugNavigatorCurrentSegmentLabel"
SEGMENT_NAME_LABEL_NODE_PREFIX = "MucusPlugNavigatorVisibleSegmentLabel_"
SEGMENT_NAME_LABEL_VIEW_NODE_ATTRIBUTE = "MucusPlugNavigator.LabelViewNodeID"
LOGICALLY_DELETED_SEGMENTS_ATTRIBUTE = (
    "MucusPlugNavigator.LogicallyDeletedSegmentIDs"
)
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
DUMMY_MODEL_SCRIPT_NAME = "dummy_mucus_model.py"
SEGMENT_NAME_LABEL_OFFSET_MINIMUM_MM = 5.0
SEGMENT_NAME_LABEL_OFFSET_FRACTION = 0.35
SEGMENT_NAME_LABEL_TEXT_SCALE = 1.0
SEGMENT_NAME_LABEL_GLYPH_SCALE = 0.05
SEGMENT_NAME_LABEL_SLICE_TOLERANCE_MM = 5.0
SEGMENT_NAME_LABEL_UPDATE_DELAY_MS = 50
SEGMENT_NAME_LABEL_CACHE_BUILD_LIMIT = 4
SEGMENT_NAME_LABEL_DEBUG = True
SLICE_CHANGE_POLL_INTERVAL_MS = 250
SEGMENT_STATUS_TAG_NAME = "Segmentation.Status"
SEGMENT_STATUS_DONE_VALUE = "completed"

ROW_MARGINS = (0, 4, 0, 4)
GRID_HORIZONTAL_SPACING = 6
GRID_VERTICAL_SPACING = 4


#
# MucusPlugNavigator
#


class MucusPlugNavigator(ScriptedLoadableModule):
    """Navigate, inspect, edit, delete, measure, export, and count mucus plugs."""

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
        self.currentSegmentLabelNode = None
        self.segmentNameLabelNodesBySegmentID = {}
        self.labelUpdateTimer = None
        self.labelCacheBuildTimer = None
        self.sliceChangePollTimer = None
        self._scheduledLabelRefreshAllowCacheBuild = False

        self._moduleIsActive = False
        self._observedSegmentation = None
        self._segmentationObserverTags = []
        self._observedDisplayNode = None
        self._displayNodeObserverTags = []
        self._observedSliceNodes = []
        self._sliceNodeObserverTags = []
        self._observedSliceViews = []
        self._sliceViewEventFiltersSupported = True
        self._lastSliceStateSignature = None
        self._sliceBaseFieldOfViewByID = {}
        self._segmentEditorAddButton = None
        self._segmentEditorShow3DButton = None
        self._suppressAutoJump = False
        self._suppressSliceLabelRefresh = False
        self._suppressSegmentationChangeRefresh = False
        self._suppressSegmentationDisplayRefresh = False
        self._sliceLabelRefreshInProgress = False

        self._initializeWidgetReferences()

    def setup(self):
        """Build the full module UI and connect it to the current Slicer scene."""
        ScriptedLoadableModuleWidget.setup(self)

        self.segmentEditorNode = self._getOrCreateSegmentEditorNode()

        self._buildSelectorsSection()
        self._buildNavigationSection()
        self._buildActionToolbarSection()
        self._buildEmbeddedSegmentEditorSection()
        self._createLabelUpdateTimer()
        self._createSliceChangePollTimer()
        self._createVisibilityShortcut()
        self._createNavigationShortcuts()
        self._connectSignals()
        self._initializeWidgetState()
        self._startSliceChangePolling()

    def cleanup(self):
        """Release observers and Segment Editor view hooks when Slicer unloads the module."""
        self._removeCurrentSegmentNameLabel()
        self._removeSliceNodeObservers()
        self._removeSliceViewEventFilters()
        self._stopSliceChangePolling()
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
        self._observeSliceNodes()
        self._observeSliceViews()
        self._startSliceChangePolling()
        if self.visibilityShortcut:
            self.visibilityShortcut.enabled = True
        self._setNavigationShortcutsEnabled(True)
        if self.segmentEditorWidget:
            self.segmentEditorWidget.setMRMLScene(slicer.mrmlScene)
            self.segmentEditorWidget.setupViewObservations()
            self.segmentEditorWidget.installKeyboardShortcuts()
            self.updateSegmentCountAndButtons()
            self.refreshVisibleSegmentNameLabels()

    def exit(self):
        """Remove Segment Editor view hooks when the user leaves the module."""
        self._moduleIsActive = False
        self._hideCurrentSegmentNameLabel()
        self._removeSliceNodeObservers()
        self._removeSliceViewEventFilters()
        self._stopSliceChangePolling()
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
        self.ctValueLabel = None
        self.spacingLabel = None
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

        self.volumeLabel = qt.QLabel("Volume: - voxels")
        controlsLayout.addWidget(self.volumeLabel, 0, 1)

        self.lengthLabel = qt.QLabel("Length: - voxels")
        controlsLayout.addWidget(self.lengthLabel, 0, 2)

        self.ctValueLabel = qt.QLabel("Median CT: -")
        controlsLayout.addWidget(self.ctValueLabel, 0, 3)

        self.spacingLabel = qt.QLabel("Voxel spacing: -")
        self.spacingLabel.setToolTip(
            "Voxel spacing of the selected source CT volume in millimeters."
        )
        controlsLayout.addWidget(self.spacingLabel, 0, 4)

        zoomLabel = qt.QLabel("Jump zoom:")
        controlsLayout.addWidget(zoomLabel, 1, 0)

        self.zoomSpinBox = qt.QDoubleSpinBox()
        self.zoomSpinBox.setRange(JUMP_ZOOM_MINIMUM, JUMP_ZOOM_MAXIMUM)
        self.zoomSpinBox.setDecimals(JUMP_ZOOM_DECIMALS)
        self.zoomSpinBox.setSingleStep(JUMP_ZOOM_STEP)
        self.zoomSpinBox.setSuffix("x")
        self.zoomSpinBox.setValue(JUMP_ZOOM_DEFAULT)
        self.zoomSpinBox.setToolTip(
            "Higher zoom uses a smaller slice field of view after jumping."
        )
        controlsLayout.addWidget(self.zoomSpinBox, 1, 1)

        self.jumpButton = self._createButton(
            "Jump",
            "Jump slice views to the currently selected mucus plug.",
        )
        controlsLayout.addWidget(self.jumpButton, 1, 2)
        self._hideBackupJumpButton()

        self.lastButton = self._createButton(
            "Last",
            "Select the previous segment in segmentation order and jump to it.",
        )
        controlsLayout.addWidget(self.lastButton, 1, 3)

        self.nextButton = self._createButton(
            "Next",
            "Select the next segment in segmentation order and jump to it.",
        )
        controlsLayout.addWidget(self.nextButton, 1, 4)

        self._setNavigationButtonIcons()
        self.layout.addWidget(controlsFrame)

    def _buildActionToolbarSection(self):
        """Create the compact action toolbar for segment and edit commands."""
        segmentToolbarFrame = qt.QFrame()
        segmentToolbarLayout = qt.QGridLayout()
        segmentToolbarFrame.setLayout(segmentToolbarLayout)
        self._configureGridLayout(segmentToolbarLayout)

        self.addButton = self._createButton(
            "Add",
            "Add a new segment to the selected segmentation.",
        )
        segmentToolbarLayout.addWidget(self.addButton, 0, 0)

        self.show3DButton = self._createButton(
            "Show 3D",
            "Toggle 3D display for the selected segmentation.",
        )
        segmentToolbarLayout.addWidget(self.show3DButton, 0, 1)

        self.visibilityButton = self._createButton(
            "Hide Seg",
            "Toggle whole segmentation visibility in 2D and 3D. Shortcut: H.",
        )
        segmentToolbarLayout.addWidget(self.visibilityButton, 0, 2)

        self.deleteButton = self._createButton(
            "Delete",
            "Delete only the currently selected mucus plug segment.",
        )
        segmentToolbarLayout.addWidget(self.deleteButton, 0, 3)

        self.measureButton = self._createButton(
            "Measure",
            "Calculate volume and length for the currently selected mucus plug.",
        )
        segmentToolbarLayout.addWidget(self.measureButton, 0, 4)

        self.noEditingButton = self._createButton(
            "No editing",
            "Turn off the active Segment Editor effect.",
        )
        segmentToolbarLayout.addWidget(self.noEditingButton, 1, 0)

        self.paintButton = self._createButton(
            "Paint",
            "Activate the Segment Editor Paint effect.",
        )
        segmentToolbarLayout.addWidget(self.paintButton, 1, 1)

        self.eraseButton = self._createButton(
            "Erase",
            "Activate the Segment Editor Erase effect.",
        )
        segmentToolbarLayout.addWidget(self.eraseButton, 1, 2)

        self.exportButton = self._createButton(
            "Export",
            "Export mucus plug measurements and source spacing to a CSV file.",
        )
        segmentToolbarLayout.addWidget(self.exportButton, 1, 3)

        self.restoreButton = self._createButton(
            "Restore",
            "Choose logically deleted mucus plug segments to show again.",
        )
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
        self.segmentationSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)",
            self.onSegmentationNodeChanged,
        )
        self.sourceVolumeSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)",
            self.onSourceVolumeNodeChanged,
        )

        self.jumpButton.connect("clicked(bool)", self.onJumpButton)
        self.lastButton.connect("clicked(bool)", self.onLastButton)
        self.nextButton.connect("clicked(bool)", self.onNextButton)
        self.addButton.connect("clicked(bool)", self.onAddButton)
        self.show3DButton.connect("clicked(bool)", self.onShow3DButton)
        self.visibilityButton.connect(
            "clicked(bool)",
            self.onSegmentationVisibilityButton,
        )
        self.deleteButton.connect("clicked(bool)", self.onDeleteButton)
        self.restoreButton.connect("clicked(bool)", self.onRestoreButton)
        self.measureButton.connect("clicked(bool)", self.onMeasureButton)
        self.noEditingButton.connect("clicked(bool)", self.onNoEditingButton)
        self.paintButton.connect("clicked(bool)", self.onPaintButton)
        self.eraseButton.connect("clicked(bool)", self.onEraseButton)
        self.exportButton.connect("clicked(bool)", self.onExportButton)

        self.zoomSpinBox.connect("valueChanged(double)", self.onZoomChanged)
        self.segmentEditorWidget.connect(
            "currentSegmentIDChanged(QString)",
            self.onCurrentSegmentChanged,
        )

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

    def _createLabelUpdateTimer(self):
        """Create a short timer so scrolling updates labels without over-refreshing."""
        self.labelUpdateTimer = qt.QTimer()
        self.labelUpdateTimer.setSingleShot(True)
        self.labelUpdateTimer.setInterval(SEGMENT_NAME_LABEL_UPDATE_DELAY_MS)
        self.labelUpdateTimer.connect("timeout()", self.onLabelUpdateTimerTimeout)

        self.labelCacheBuildTimer = qt.QTimer()
        self.labelCacheBuildTimer.setSingleShot(True)
        self.labelCacheBuildTimer.setInterval(SEGMENT_NAME_LABEL_UPDATE_DELAY_MS)
        self.labelCacheBuildTimer.connect(
            "timeout()",
            self.onLabelCacheBuildTimerTimeout,
        )

    def _createSliceChangePollTimer(self):
        """Create a polling fallback for slice changes that do not emit events here."""
        self.sliceChangePollTimer = qt.QTimer()
        self.sliceChangePollTimer.setSingleShot(False)
        self.sliceChangePollTimer.setInterval(SLICE_CHANGE_POLL_INTERVAL_MS)
        self.sliceChangePollTimer.connect("timeout()", self.onSliceChangePollTimer)

    def _createVisibilityShortcut(self):
        """Bind the H key to the whole-segmentation visibility button."""
        shortcutParent = self._shortcutParent()
        self.visibilityShortcut = qt.QShortcut(shortcutParent)
        self.visibilityShortcut.setKey(self._visibilityShortcutKeySequence())
        self.visibilityShortcut.setContext(qt.Qt.ApplicationShortcut)
        if hasattr(self.visibilityShortcut, "setAutoRepeat"):
            self.visibilityShortcut.setAutoRepeat(False)
        self.visibilityShortcut.connect(
            "activated()",
            self.onSegmentationVisibilityShortcut,
        )
        self.visibilityShortcut.connect(
            "activatedAmbiguously()",
            self.onSegmentationVisibilityShortcut,
        )

    def _createNavigationShortcuts(self):
        """Bind Left and Right Arrow keys to the Last and Next navigation buttons."""
        shortcutParent = self._shortcutParent()
        self.lastShortcut = self._createShortcut(
            shortcutParent,
            qt.Qt.Key_Left,
            "Left",
            self.onLastShortcut,
        )
        self.nextShortcut = self._createShortcut(
            shortcutParent,
            qt.Qt.Key_Right,
            "Right",
            self.onNextShortcut,
        )

    def _shortcutParent(self):
        """Return the preferred Qt parent for module-level shortcuts."""
        mainWindow = slicer.util.mainWindow()
        return mainWindow if mainWindow else self.parent

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
        node = slicer.mrmlScene.GetSingletonNode(
            SEGMENT_EDITOR_SINGLETON_TAG,
            "vtkMRMLSegmentEditorNode",
        )
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
            self.segmentEditorWidget.setEffectButtonStyle(
                qt.Qt.ToolButtonTextBesideIcon
            )
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
        self._copyButtonIconSize(
            show3DButton if show3DButton else addButton,
            self.visibilityButton,
        )
        self._copyButtonIconSize(
            removeButton if removeButton else addButton,
            self.deleteButton,
        )
        self._copyButtonIconSize(addButton, self.restoreButton)
        self._copyButtonIconSize(addButton, self.measureButton)
        self._copyButtonIconSize(
            noneButton if noneButton else addButton,
            self.noEditingButton,
        )
        self._copyButtonIconSize(
            paintButton if paintButton else addButton,
            self.paintButton,
        )
        self._copyButtonIconSize(
            eraseButton if eraseButton else addButton,
            self.eraseButton,
        )
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
        """Copy icon size from Segment Editor without copying layout constraints."""
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
        """Hide the original effect grid so only the custom edit buttons show."""
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
        if (
            parentCounts[mostLikelyEffectGrid] >= 2
            and mostLikelyEffectGrid != self.segmentEditorWidget
            and not containsToolbarButton
        ):
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
        self.ensureSliceChangeMonitoringActive()
        self.segmentEditorWidget.setSegmentationNode(segmentationNode)
        self.logic.ensureSegmentationVisible(segmentationNode)
        self._observeSegmentation(segmentationNode)
        self._selectFirstSegmentIfNeeded()
        self._hideCurrentSegmentNameLabel()
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()
        self.refreshVisibleSegmentNameLabels()

    def onSourceVolumeNodeChanged(self, sourceVolumeNode):
        """Update Segment Editor and slice background when the source CT volume changes."""
        self.ensureSliceChangeMonitoringActive()
        if hasattr(self.segmentEditorWidget, "setSourceVolumeNode"):
            self.segmentEditorWidget.setSourceVolumeNode(sourceVolumeNode)
        else:
            self.segmentEditorWidget.setMasterVolumeNode(sourceVolumeNode)
        self.logic.ensureSourceVolumeVisible(sourceVolumeNode)
        self._sliceBaseFieldOfViewByID = {}
        self.updateSourceVoxelSpacing()
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()

    def onCurrentSegmentChanged(self, segmentID):
        """Clear stale measurements, refresh buttons, and auto-jump after user segment clicks."""
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()
        if (
            not self._suppressAutoJump
            and self.logic.isActiveSegmentID(self.segmentationNode(), segmentID)
        ):
            self.debugSegmentLabelMessage(
                "Segment list click: selected={}".format(
                    self.logic.segmentName(self.segmentationNode(), segmentID)
                )
            )
            self.jumpToSegment(
                segmentID,
                labelRefreshReason="Segment list click all-view check",
            )

    def onObservedSegmentationChanged(self, caller=None, event=None):
        """Refresh the UI when segments are added, removed, reordered, or modified."""
        if self._suppressSegmentationChangeRefresh:
            self.debugSegmentLabelMessage(
                "segmentation change ignored during status-only update"
            )
            return
        self._selectFirstSegmentIfNeeded()
        if not self.logic.isActiveSegmentID(self.segmentationNode(), self.currentSegmentID()):
            self._hideCurrentSegmentNameLabel()
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()
        self.refreshVisibleSegmentNameLabels()

    def onZoomChanged(self, value):
        """Apply slice zoom and resize labels without recalculating visible segment IDs."""
        self._suppressSliceLabelRefresh = True
        try:
            self.logic.ensureSliceFieldOfViewBaseline(self._sliceBaseFieldOfViewByID)
            self.logic.applySliceZoom(value, self._sliceBaseFieldOfViewByID)
            self.updateVisibleSegmentNameLabelTextScale(value)
        finally:
            qt.QTimer.singleShot(100, self._clearSliceLabelRefreshSuppression)

    def _clearSliceLabelRefreshSuppression(self):
        """Allow slice scrolling to refresh labels after a zoom-only change finishes."""
        self._suppressSliceLabelRefresh = False

    def updateVisibleSegmentNameLabelTextScale(self, zoomFactor):
        """Resize existing segment-name labels without recalculating visible segments."""
        textScale = SEGMENT_NAME_LABEL_TEXT_SCALE * max(float(zoomFactor), 1.0)
        for labelNode in self._allSegmentNameLabelNodes():
            displayNode = labelNode.GetDisplayNode() if labelNode else None
            if not displayNode or not hasattr(displayNode, "SetTextScale"):
                continue
            displayNode.SetTextScale(textScale)
            displayNode.Modified()

    def onJumpButton(self, checked=False):
        """Jump slice views to the currently selected segment."""
        segmentID = self.currentSegmentID()
        if not self.logic.isActiveSegmentID(self.segmentationNode(), segmentID):
            self._selectFirstSegmentIfNeeded()
            segmentID = self.currentSegmentID()
        self.debugSegmentLabelMessage(
            "Jump button: target={}".format(
                self.logic.segmentName(self.segmentationNode(), segmentID)
            )
        )
        self.jumpToSegment(segmentID, labelRefreshReason="Jump button all-view check")

    def onLastButton(self, checked=False):
        """Select the previous segment in segmentation order and jump to it."""
        segmentationNode = self.segmentationNode()
        currentSegmentID = self.selectedOrCurrentSegmentID()
        if self.logic.isActiveSegmentID(segmentationNode, currentSegmentID):
            self._setCurrentSegmentIDWithoutAutoJump(currentSegmentID)
        previousSegmentID = self.logic.previousSegmentID(
            segmentationNode,
            currentSegmentID,
            wrap=True,
        )
        if not previousSegmentID:
            return
        self.debugSegmentLabelMessage(
            "Last button: current={} target={}".format(
                self.logic.segmentName(segmentationNode, currentSegmentID),
                self.logic.segmentName(segmentationNode, previousSegmentID),
            )
        )
        self._setCurrentSegmentIDWithoutAutoJump(previousSegmentID)
        self.jumpToSegment(
            previousSegmentID,
            labelRefreshReason="Last button all-view check",
        )
        self.updateSegmentCountAndButtons()

    def onNextButton(self, checked=False):
        """Select the next segment in segmentation order and jump to it."""
        segmentationNode = self.segmentationNode()
        currentSegmentID = self.selectedOrCurrentSegmentID()
        if self.logic.isActiveSegmentID(segmentationNode, currentSegmentID):
            self._setCurrentSegmentIDWithoutAutoJump(currentSegmentID)
        nextSegmentID = self.logic.nextSegmentID(
            segmentationNode,
            currentSegmentID,
            wrap=True,
        )
        if not nextSegmentID:
            return
        self.debugSegmentLabelMessage(
            "Next button: current={} target={} markDone=True".format(
                self.logic.segmentName(segmentationNode, currentSegmentID),
                self.logic.segmentName(segmentationNode, nextSegmentID),
            )
        )
        self._suppressSegmentationChangeRefresh = True
        try:
            self.logic.markSegmentDone(segmentationNode, currentSegmentID)
        finally:
            self._suppressSegmentationChangeRefresh = False
        self._setCurrentSegmentIDWithoutAutoJump(nextSegmentID)
        self.jumpToSegment(
            nextSegmentID,
            labelRefreshReason="Next button all-view check",
        )
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
        segmentationNode = self.segmentationNode()
        previousSegmentIDs = set(self.logic.activeSegmentIDs(segmentationNode))

        if self._segmentEditorAddButton:
            self._runWithoutAutoJump(self._segmentEditorAddButton.click)
        else:
            if segmentationNode:
                segmentID = segmentationNode.GetSegmentation().AddEmptySegment()
                self._setCurrentSegmentIDWithoutAutoJump(segmentID)

        newSegmentID = self._newSegmentIDAfterAdd(
            segmentationNode,
            previousSegmentIDs,
        )
        self.logic.renameSegmentIfNameConflictsWithDeletedBackup(
            segmentationNode,
            newSegmentID,
        )
        self.updateSegmentCountAndButtons()

    def _newSegmentIDAfterAdd(self, segmentationNode, previousSegmentIDs):
        """Return the segment ID most likely created by the last Add action."""
        currentSegmentID = self.currentSegmentID()
        if (
            self.logic.isActiveSegmentID(segmentationNode, currentSegmentID)
            and currentSegmentID not in previousSegmentIDs
        ):
            return currentSegmentID

        for segmentID in self.logic.activeSegmentIDs(segmentationNode):
            if segmentID not in previousSegmentIDs:
                return segmentID
        return ""

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
        startTime = time.time()
        wasVisible = self.logic.isSegmentationVisible(segmentationNode)
        visible = not wasVisible
        self.debugSegmentLabelMessage(
            "Hide/Show Seg button: start current={} target={}".format(
                "visible" if wasVisible else "hidden",
                "visible" if visible else "hidden",
            )
        )
        visibilityStartTime = time.time()
        self._suppressSegmentationDisplayRefresh = True
        try:
            self.logic.setSegmentationVisible(segmentationNode, visible)
            visible = self.logic.isSegmentationVisible(segmentationNode)
        finally:
            self._suppressSegmentationDisplayRefresh = False
        self.debugSegmentLabelMessage(
            "Hide/Show Seg button: Slicer visibility toggle took {:.3f}s".format(
                time.time() - visibilityStartTime
            )
        )
        self.debugSegmentLabelMessage(
            "Hide/Show Seg button: segmentation is now {}".format(
                "visible" if visible else "hidden"
            )
        )
        if visible:
            labelStartTime = time.time()
            self.refreshVisibleSegmentNameLabels(reason="Hide/Show Seg button refresh")
            self.debugSegmentLabelMessage(
                "Hide/Show Seg button: label refresh took {:.3f}s".format(
                    time.time() - labelStartTime
                )
            )
        else:
            labelStartTime = time.time()
            self._hideCurrentSegmentNameLabel()
            self.debugSegmentLabelMessage(
                "Hide/Show Seg button: hiding labels took {:.3f}s".format(
                    time.time() - labelStartTime
                )
            )
        self.updateSegmentCountAndButtons()
        self.debugSegmentLabelMessage(
            "Hide/Show Seg button: total took {:.3f}s".format(
                time.time() - startTime
            )
        )
        return visible

    def onSegmentationVisibilityShortcut(self):
        """Toggle whole-segmentation visibility when the user presses H."""
        blockingReason = self._visibilityShortcutBlockingReason()
        if blockingReason:
            self._showShortcutMessage(blockingReason)
            return
        self.debugSegmentLabelMessage("H shortcut: pressed")
        visible = self.onSegmentationVisibilityButton()
        self._showShortcutMessage(
            "Mucus segmentation is now {}.".format(
                "visible" if visible else "hidden"
            )
        )

    def onDeleteButton(self, checked=False):
        """Logically delete the current segment by moving it to the hidden restore backup."""
        segmentationNode = self.segmentationNode()
        segmentID = self.currentSegmentID()
        if not self.logic.isValidSegmentID(segmentationNode, segmentID):
            return
        if self.logic.isLogicallyDeletedSegment(segmentationNode, segmentID):
            self._showShortcutMessage(
                "This mucus plug segment is already logically deleted."
            )
            return

        segment = segmentationNode.GetSegmentation().GetSegment(segmentID)
        segmentName = segment.GetName() if segment else segmentID
        answer = qt.QMessageBox.question(
            slicer.util.mainWindow(),
            "Delete mucus plug segment",
            (
                "Move this mucus plug segment to the deleted list?\n"
                "It will disappear from the segment table, but you can restore it later."
                "\n\n{}".format(segmentName)
            ),
            qt.QMessageBox.Yes | qt.QMessageBox.No,
            qt.QMessageBox.No,
        )
        if answer != qt.QMessageBox.Yes:
            return

        nextSegmentID = self.logic.logicalDeleteSegmentAndGetNearby(
            segmentationNode,
            segmentID,
        )
        self._setCurrentSegmentIDWithoutAutoJump(nextSegmentID if nextSegmentID else "")
        self.updateSegmentCountAndButtons()
        if nextSegmentID:
            self.debugSegmentLabelMessage(
                "Delete button: removed={} target={}".format(
                    segmentName,
                    self.logic.segmentName(segmentationNode, nextSegmentID),
                )
            )
            self.jumpToSegment(
                nextSegmentID,
                labelRefreshReason="Delete button all-view check",
            )

    def onRestoreButton(self, checked=False):
        """Show a chooser for logically deleted segments and restore the selected ones."""
        segmentationNode = self.segmentationNode()
        deletedSegmentIDs = self.logic.logicallyDeletedSegmentIDs(segmentationNode)
        if not deletedSegmentIDs:
            self._showShortcutMessage("No logically deleted mucus plug segments to restore.")
            return

        selectedSegmentIDs = self._promptForDeletedSegmentsToRestore(
            segmentationNode,
            deletedSegmentIDs,
        )
        if selectedSegmentIDs is None:
            return
        if not selectedSegmentIDs:
            self._showShortcutMessage("No mucus plug segments were selected to restore.")
            return

        restoredSegmentIDs = self.logic.restoreLogicallyDeletedSegments(
            segmentationNode,
            selectedSegmentIDs,
        )
        self.updateSegmentCountAndButtons()
        if not restoredSegmentIDs:
            self._showShortcutMessage("No selected mucus plug segments could be restored.")
            return

        firstRestoredSegmentID = restoredSegmentIDs[0]
        self._setCurrentSegmentIDWithoutAutoJump(firstRestoredSegmentID)
        self.debugSegmentLabelMessage(
            "Restore button: restored={} target={}".format(
                len(restoredSegmentIDs),
                self.logic.segmentName(segmentationNode, firstRestoredSegmentID),
            )
        )
        self.jumpToSegment(
            firstRestoredSegmentID,
            labelRefreshReason="Restore button all-view check",
        )
        self._showShortcutMessage(
            "Restored {} logically deleted mucus plug segment(s).".format(
                len(restoredSegmentIDs)
            )
        )

    def _promptForDeletedSegmentsToRestore(self, segmentationNode, deletedSegmentIDs):
        """Ask the user which logically deleted segment IDs should be restored."""
        dialog = qt.QDialog(slicer.util.mainWindow())
        dialog.setWindowTitle("Restore mucus plug segments")

        layout = qt.QVBoxLayout()
        dialog.setLayout(layout)
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

        buttonFrame = qt.QFrame()
        buttonLayout = qt.QHBoxLayout()
        buttonFrame.setLayout(buttonLayout)
        buttonLayout.addStretch(1)
        restoreButton = qt.QPushButton("Restore selected")
        cancelButton = qt.QPushButton("Cancel")
        restoreButton.connect("clicked(bool)", lambda checked=False: dialog.accept())
        cancelButton.connect("clicked(bool)", lambda checked=False: dialog.reject())
        buttonLayout.addWidget(restoreButton)
        buttonLayout.addWidget(cancelButton)
        layout.addWidget(buttonFrame)

        if dialog.exec_() != qt.QDialog.Accepted:
            return None
        return [str(item.data(qt.Qt.UserRole)) for item in listWidget.selectedItems()]

    def _segmentColorIcon(self, segment, backupNode=None, segmentID=""):
        """Create a small color-square icon for a deleted segment list item."""
        color = self.logic.deletedSegmentColor(backupNode, segmentID, segment)

        pixmap = qt.QPixmap(18, 18)
        pixmap.fill(
            qt.QColor(
                int(color[0] * 255),
                int(color[1] * 255),
                int(color[2] * 255),
            )
        )
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
        self.ctValueLabel.setText("Median CT: calculating...")
        slicer.app.processEvents()

        metrics = self.logic.segmentVoxelMetrics(
            segmentationNode,
            segmentID,
            self.sourceVolumeNode(),
            includeMedianCTValue=True,
        )
        if not metrics:
            self.volumeLabel.setText("Volume: failed")
            self.lengthLabel.setText("Length: failed")
            self.ctValueLabel.setText("Median CT: failed")
            return

        self.volumeLabel.setText(
            "Volume: {} voxels / {} mm3".format(
                metrics["volumePixels"],
                metrics["volumeMm3Text"],
            )
        )
        self.lengthLabel.setText(
            "Length: {} voxels / {} mm".format(
                metrics["lengthPixels"],
                metrics["lengthMmText"],
            )
        )
        self.ctValueLabel.setText(
            "Median CT: {}".format(metrics["medianCTValueText"])
        )

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
        """Export active segment names, measurements, and CT spacing to CSV."""
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
            exportedCount, skippedCount = self._writeMeasurementsCsv(
                filePath,
                segmentationNode,
            )
            message = "Exported {} mucus plug measurements to:\n{}".format(
                exportedCount,
                filePath,
            )
            if skippedCount:
                message += "\n\nSkipped {} large mask-like segment(s).".format(skippedCount)
            slicer.util.infoDisplay(message)
        except Exception as exc:
            logging.exception("Failed to export mucus plug measurements")
            if self._isPermissionDeniedError(exc):
                self._showExportPermissionError(filePath)
            else:
                slicer.util.errorDisplay(
                    "Failed to export mucus plug measurements:\n{}".format(exc)
                )
        finally:
            self._setExportInProgress(False)

    def _isPermissionDeniedError(self, exception):
        """Return True when export failed because the target file cannot be written."""
        return (
            isinstance(exception, PermissionError)
            or getattr(exception, "errno", None) == 13
        )

    def _showExportPermissionError(self, filePath):
        """Show a clear export error when the CSV file is locked or read-only."""
        slicer.util.errorDisplay(
            (
                "Could not replace the CSV file.\n\n"
                "The file may already be open in Excel or another program. "
                "Please close the CSV file, then export again.\n\n"
                "File:\n{}"
            ).format(filePath)
        )

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
        sourceVolumeNode = self.sourceVolumeNode()
        rows, skippedRows = self.logic.exportMucusPlugMeasurementRows(
            segmentationNode,
            sourceVolumeNode,
        )
        with open(filePath, "w", newline="") as csvFile:
            writer = csv.writer(csvFile)
            writer.writerow(["Mucus plug count", len(rows)])
            writer.writerow(
                ["Source voxel spacing (mm)"]
                + self.logic.volumeSpacingTextValues(sourceVolumeNode)
            )
            writer.writerow([])
            writer.writerow(
                [
                    "Segment",
                    "Volume (voxels)",
                    "Volume (mm3)",
                    "Length (voxels)",
                    "Length (mm)",
                    "Median CT",
                ]
            )
            for row in rows:
                writer.writerow(
                    [
                        row["segmentName"],
                        row["volumePixels"],
                        row["volumeMm3Text"],
                        row["lengthPixels"],
                        row["lengthMmText"],
                        row["medianCTValueText"],
                    ]
                )
        return len(rows), len(skippedRows)

    def jumpToSegment(self, segmentID, labelRefreshReason="Jump all-view check"):
        """Center slice views on a segment and apply the current jump zoom."""
        segmentationNode = self.segmentationNode()
        if not self.logic.isActiveSegmentID(segmentationNode, segmentID):
            self._hideCurrentSegmentNameLabel()
            return
        self.logic.ensureSourceVolumeVisible(self.sourceVolumeNode())
        self._suppressSliceLabelRefresh = True
        try:
            didJump = self.logic.jumpToSegment(
                segmentationNode,
                segmentID,
                self.zoomSpinBox.value,
                self._sliceBaseFieldOfViewByID,
            )
        finally:
            self._suppressSliceLabelRefresh = False
        if didJump:
            self.refreshVisibleSegmentNameLabelsAfterSliceChange(
                reason=labelRefreshReason,
            )
        else:
            self._hideCurrentSegmentNameLabel()

    def refreshVisibleSegmentNameLabelsAfterSliceChange(self, reason="slice change full check"):
        """Run the complete current-slice segment-label decision after any slice move."""
        self.refreshVisibleSegmentNameLabelsForSliceNodes(
            self.currentSliceNodes(),
            reason=reason,
        )

    def refreshVisibleSegmentNameLabelsForSliceNode(self, sliceNode, reason):
        """Refresh labels only for one changed slice view."""
        if not sliceNode:
            self.refreshVisibleSegmentNameLabelsAfterSliceChange()
            return
        self.refreshVisibleSegmentNameLabelsForSliceNodes(
            [sliceNode],
            reason=reason,
            viewNodeIDToReplace=sliceNode.GetID(),
        )

    def refreshVisibleSegmentNameLabelsForSliceNodes(
        self,
        sliceNodes,
        reason,
        viewNodeIDToReplace=None,
    ):
        """Run the label decision for the provided slice nodes."""
        self.ensureSliceChangeMonitoringActive()
        if self._sliceLabelRefreshInProgress:
            self.debugSegmentLabelMessage("skip full check: refresh already running")
            return
        self._sliceLabelRefreshInProgress = True
        try:
            self.refreshVisibleSegmentNameLabels(
                allowCacheBuild=True,
                schedulePending=False,
                cacheBuildLimit=None,
                reason=reason,
                sliceNodes=sliceNodes,
                viewNodeIDToReplace=viewNodeIDToReplace,
            )
            self._lastSliceStateSignature = self.currentSliceStateSignature()
        finally:
            self._sliceLabelRefreshInProgress = False

    def _updateCurrentSegmentNameLabel(self, segmentationNode, segmentID):
        """Refresh visible segment labels; kept as a compatibility wrapper."""
        self.refreshVisibleSegmentNameLabels()

    def refreshVisibleSegmentNameLabels(
        self,
        allowCacheBuild=False,
        schedulePending=True,
        cacheBuildLimit=SEGMENT_NAME_LABEL_CACHE_BUILD_LIMIT,
        reason="refresh",
        sliceNodes=None,
        viewNodeIDToReplace=None,
    ):
        """Show labels for active segments intersecting the current slice views."""
        startTime = time.time()
        segmentationNode = self.segmentationNode()
        if (
            not segmentationNode
            or not self.logic.isSegmentationVisible(segmentationNode)
        ):
            self.debugSegmentLabelMessage(
                "{}: hide labels because segmentation is missing or hidden".format(
                    reason
                )
            )
            self._hideCurrentSegmentNameLabel()
            return

        self.debugSegmentLabelMessage(
            "{}: start label check allowCacheBuild={} cacheBuildLimit={}".format(
                reason,
                allowCacheBuild,
                cacheBuildLimit,
            )
        )
        labelEntries = self.logic.visibleSegmentLabelEntries(
            segmentationNode,
            sliceNodes if sliceNodes is not None else self.currentSliceNodes(),
            self.sourceVolumeNode(),
            allowCacheBuild=allowCacheBuild,
            cacheBuildLimit=cacheBuildLimit,
        )
        debugSummary = self.logic.lastSegmentLabelDebugSummary()
        elapsedSeconds = time.time() - startTime
        self.debugSegmentLabelMessage(
            "{}: finished in {:.3f}s; labels={}; {}".format(
                reason,
                elapsedSeconds,
                len(labelEntries),
                debugSummary,
            )
        )
        if not labelEntries:
            if self.sourceVolumeNode():
                if schedulePending and self.logic.segmentLabelCacheBuildPending():
                    self.debugSegmentLabelMessage(
                        "{}: no labels yet; cache build pending".format(reason)
                    )
                    self.scheduleSegmentLabelCacheBuild()
                    return
                self.debugSegmentLabelMessage(
                    "{}: hide labels because full check found 0 labels".format(reason)
                )
                self._hideSegmentNameLabelsForView(viewNodeIDToReplace)
                return
            fallbackSegmentID = self.selectedOrCurrentSegmentID()
            if self.logic.isActiveSegmentID(segmentationNode, fallbackSegmentID):
                self._showSingleSegmentNameLabel(segmentationNode, fallbackSegmentID)
            return
        if not self.logic.segmentLabelCacheBuildPending():
            self._hideSegmentNameLabelsForView(viewNodeIDToReplace)
        for labelEntry in labelEntries:
            labelNode = self._getOrCreateSegmentNameLabelNode(labelEntry["labelID"])
            self._setSingleMarkupLabelPoint(
                labelNode,
                labelEntry["positionRAS"],
                labelEntry["segmentName"],
            )
            self._configureCurrentSegmentNameLabelDisplay(
                labelNode,
                visible=True,
                color=labelEntry["color"],
                zoomFactor=self.zoomSpinBox.value,
                viewNodeID=labelEntry["viewNodeID"],
            )
        if schedulePending and self.logic.segmentLabelCacheBuildPending():
            self.scheduleSegmentLabelCacheBuild()

    def debugSegmentLabelMessage(self, message):
        """Print label-refresh diagnostics to the Slicer console."""
        if not SEGMENT_NAME_LABEL_DEBUG:
            return
        fullMessage = "[MucusPlugNavigator label debug] {}".format(message)
        print(fullMessage)
        logging.info(fullMessage)

    def scheduleVisibleSegmentNameLabelRefresh(
        self,
        force=False,
        allowCacheBuild=False,
    ):
        """Refresh visible labels after slice movement settles."""
        if self._moduleIsActive and self.labelUpdateTimer:
            if self.labelUpdateTimer.isActive() and not force:
                return
            self._scheduledLabelRefreshAllowCacheBuild = allowCacheBuild
            self.labelUpdateTimer.start()
        else:
            self.refreshVisibleSegmentNameLabels(allowCacheBuild=allowCacheBuild)

    def onLabelUpdateTimerTimeout(self):
        """Refresh labels from the timer using the scheduled cache-build mode."""
        self.refreshVisibleSegmentNameLabels(
            allowCacheBuild=self._scheduledLabelRefreshAllowCacheBuild,
        )

    def scheduleSegmentLabelCacheBuild(self, force=False):
        """Schedule a small background chunk to build missing segment-label cache."""
        if self._moduleIsActive and self.labelCacheBuildTimer:
            if self.labelCacheBuildTimer.isActive() and not force:
                return
            self.labelCacheBuildTimer.start()
        else:
            self.refreshVisibleSegmentNameLabels(
                allowCacheBuild=True,
                schedulePending=True,
                cacheBuildLimit=SEGMENT_NAME_LABEL_CACHE_BUILD_LIMIT,
            )

    def onLabelCacheBuildTimerTimeout(self):
        """Build a small cache chunk, then refresh labels for the current slice."""
        self.refreshVisibleSegmentNameLabels(
            allowCacheBuild=True,
            schedulePending=True,
            cacheBuildLimit=SEGMENT_NAME_LABEL_CACHE_BUILD_LIMIT,
        )

    def _getOrCreateSegmentNameLabelNode(self, segmentID):
        """Return a hidden singleton markups node for one segment label."""
        if segmentID in self.segmentNameLabelNodesBySegmentID:
            return self.segmentNameLabelNodesBySegmentID[segmentID]

        labelNodeName = self._segmentNameLabelNodeName(segmentID)
        try:
            labelNode = slicer.util.getNode(labelNodeName)
        except Exception:
            labelNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode",
                labelNodeName,
            )
        try:
            labelNode.HideFromEditorsOn()
        except Exception:
            logging.debug("Could not hide segment label node", exc_info=True)
        self.segmentNameLabelNodesBySegmentID[segmentID] = labelNode
        return labelNode

    def _segmentNameLabelNodeName(self, segmentID):
        """Return the markups node name used for one segment label."""
        safeSegmentID = re.sub(r"[^A-Za-z0-9_]+", "_", str(segmentID))
        return "{}{}".format(SEGMENT_NAME_LABEL_NODE_PREFIX, safeSegmentID)

    def _getOrCreateCurrentSegmentNameLabelNode(self):
        """Return the legacy current-segment label node for older call paths."""
        labelNode = self._getOrCreateSegmentNameLabelNode("current")
        self.currentSegmentLabelNode = labelNode
        return labelNode

    def _showSingleSegmentNameLabel(self, segmentationNode, segmentID):
        """Show one segment label near the mucus plug without covering it."""
        labelPositionRAS = self.logic.segmentLabelPositionRAS(
            segmentationNode,
            segmentID,
        )
        if labelPositionRAS is None:
            self._hideCurrentSegmentNameLabel()
            return

        labelNode = self._getOrCreateSegmentNameLabelNode(segmentID)
        segmentName = self.logic.segmentName(segmentationNode, segmentID)
        segmentColor = self.logic.segmentDisplayColor(segmentationNode, segmentID)
        self._setSingleMarkupLabelPoint(labelNode, labelPositionRAS, segmentName)
        self._configureCurrentSegmentNameLabelDisplay(
            labelNode,
            visible=True,
            color=segmentColor,
            zoomFactor=self.zoomSpinBox.value,
        )

    def _setSingleMarkupLabelPoint(self, labelNode, positionRAS, labelText):
        """Replace the label node contents with one labeled RAS point."""
        if hasattr(labelNode, "RemoveAllControlPoints"):
            labelNode.RemoveAllControlPoints()
        elif hasattr(labelNode, "RemoveAllMarkups"):
            labelNode.RemoveAllMarkups()

        try:
            labelNode.AddControlPointWorld(
                vtk.vtkVector3d(positionRAS[0], positionRAS[1], positionRAS[2]),
                labelText,
            )
        except Exception:
            try:
                labelNode.AddFiducial(positionRAS[0], positionRAS[1], positionRAS[2])
            except Exception:
                labelNode.AddFiducialFromArray(positionRAS)
            if hasattr(labelNode, "SetNthControlPointLabel"):
                labelNode.SetNthControlPointLabel(0, labelText)
            elif hasattr(labelNode, "SetNthFiducialLabel"):
                labelNode.SetNthFiducialLabel(0, labelText)
        if hasattr(labelNode, "SetNthControlPointVisibility"):
            labelNode.SetNthControlPointVisibility(0, True)
        if hasattr(labelNode, "SetDisplayVisibility"):
            labelNode.SetDisplayVisibility(True)
        labelNode.Modified()

    def _configureCurrentSegmentNameLabelDisplay(
        self,
        labelNode,
        visible,
        color=None,
        zoomFactor=None,
        viewNodeID=None,
    ):
        """Configure the current segment label to appear in slice views."""
        labelNode.CreateDefaultDisplayNodes()
        displayNode = labelNode.GetDisplayNode()
        if not displayNode:
            return
        color = color if color else [1.0, 1.0, 1.0]
        textScale = SEGMENT_NAME_LABEL_TEXT_SCALE * max(
            float(zoomFactor) if zoomFactor else 1.0,
            1.0,
        )
        if hasattr(labelNode, "SetDisplayVisibility"):
            labelNode.SetDisplayVisibility(bool(visible))
        displayNode.SetVisibility(bool(visible))
        if hasattr(displayNode, "SetVisibility2D"):
            displayNode.SetVisibility2D(bool(visible))
        if hasattr(displayNode, "SetVisibility3D"):
            displayNode.SetVisibility3D(bool(visible))
        if hasattr(displayNode, "SetPointLabelsVisibility"):
            displayNode.SetPointLabelsVisibility(True)
        if hasattr(displayNode, "SetTextScale"):
            displayNode.SetTextScale(textScale)
        if hasattr(displayNode, "SetGlyphScale"):
            displayNode.SetGlyphScale(SEGMENT_NAME_LABEL_GLYPH_SCALE)
        self._setMarkupDisplayColor(displayNode, color)
        self._setSegmentNameLabelViewAttribute(labelNode, viewNodeID)
        self._setMarkupSliceProjection(displayNode, visible, color)
        self._setMarkupViewRestriction(displayNode, viewNodeID)
        if hasattr(labelNode, "SetLocked"):
            labelNode.SetLocked(False)
        labelNode.Modified()
        displayNode.Modified()

    def _setMarkupDisplayColor(self, displayNode, color):
        """Set markup display color using APIs available in the current Slicer."""
        for methodName in ("SetSelectedColor", "SetColor", "SetGlyphColor"):
            if hasattr(displayNode, methodName):
                try:
                    getattr(displayNode, methodName)(color[0], color[1], color[2])
                except Exception:
                    logging.debug("Could not set markup color", exc_info=True)

    def _setMarkupSliceProjection(self, displayNode, visible, color):
        """Disable slice projection so labels only show on slices with visible pixels."""
        if hasattr(displayNode, "SetSliceProjection"):
            displayNode.SetSliceProjection(False)
        if hasattr(displayNode, "SetSliceProjectionUseFiducialColor"):
            displayNode.SetSliceProjectionUseFiducialColor(True)
        if hasattr(displayNode, "SetSliceProjectionOutlinedBehindSlicePlane"):
            displayNode.SetSliceProjectionOutlinedBehindSlicePlane(False)
        if hasattr(displayNode, "SetSliceProjectionColor"):
            displayNode.SetSliceProjectionColor(color[0], color[1], color[2])

    def _setMarkupViewRestriction(self, displayNode, viewNodeID):
        """Restrict a label to one slice view when the display node supports it."""
        try:
            if hasattr(displayNode, "RemoveAllViewNodeIDs"):
                displayNode.RemoveAllViewNodeIDs()
            if not viewNodeID:
                return
            if hasattr(displayNode, "AddViewNodeID"):
                displayNode.AddViewNodeID(viewNodeID)
                return
            if hasattr(displayNode, "SetViewNodeIDs"):
                viewNodeIDs = vtk.vtkStringArray()
                viewNodeIDs.InsertNextValue(viewNodeID)
                displayNode.SetViewNodeIDs(viewNodeIDs)
        except Exception:
            logging.debug("Could not restrict segment label to a slice view", exc_info=True)

    def _setSegmentNameLabelViewAttribute(self, labelNode, viewNodeID):
        """Store which slice view owns a label so one view can refresh independently."""
        if not labelNode or not hasattr(labelNode, "SetAttribute"):
            return
        labelNode.SetAttribute(
            SEGMENT_NAME_LABEL_VIEW_NODE_ATTRIBUTE,
            viewNodeID if viewNodeID else "",
        )

    def _hideCurrentSegmentNameLabel(self):
        """Hide all segment name labels without deleting their markups nodes."""
        self._hideSegmentNameLabelsForView(None)

    def _hideSegmentNameLabelsForView(self, viewNodeID):
        """Hide all labels, or only labels owned by one slice view."""
        for labelNode in self._allSegmentNameLabelNodes():
            if viewNodeID and not self._isSegmentNameLabelForView(labelNode, viewNodeID):
                continue
            self._configureCurrentSegmentNameLabelDisplay(labelNode, visible=False)

    def _isSegmentNameLabelForView(self, labelNode, viewNodeID):
        """Return True when a label node belongs to one slice view."""
        if not labelNode or not viewNodeID:
            return False
        try:
            return labelNode.GetAttribute(SEGMENT_NAME_LABEL_VIEW_NODE_ATTRIBUTE) == viewNodeID
        except Exception:
            return False

    def _removeCurrentSegmentNameLabel(self):
        """Delete all segment label nodes during module cleanup."""
        for labelNode in self._allSegmentNameLabelNodes():
            slicer.mrmlScene.RemoveNode(labelNode)
        self.currentSegmentLabelNode = None
        self.segmentNameLabelNodesBySegmentID = {}

    def _allSegmentNameLabelNodes(self):
        """Return every markups node created for segment name labels."""
        labelNodes = []
        for labelNode in self.segmentNameLabelNodesBySegmentID.values():
            if labelNode and labelNode not in labelNodes:
                labelNodes.append(labelNode)

        for labelNode in slicer.util.getNodesByClass("vtkMRMLMarkupsFiducialNode"):
            nodeName = labelNode.GetName() if labelNode else ""
            if (
                nodeName == SEGMENT_NAME_LABEL_NODE_NAME
                or nodeName.startswith(SEGMENT_NAME_LABEL_NODE_PREFIX)
            ) and labelNode not in labelNodes:
                labelNodes.append(labelNode)
        return labelNodes

    def currentSliceNodes(self):
        """Return slice nodes currently shown in the layout."""
        layoutManager = slicer.app.layoutManager()
        sliceNodes = []
        if layoutManager:
            for sliceViewName in layoutManager.sliceViewNames():
                sliceWidget = layoutManager.sliceWidget(sliceViewName)
                sliceNode = sliceWidget.mrmlSliceNode() if sliceWidget else None
                if sliceNode and sliceNode not in sliceNodes:
                    sliceNodes.append(sliceNode)
        if sliceNodes:
            return sliceNodes
        return slicer.util.getNodesByClass("vtkMRMLSliceNode")

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

    def selectedOrCurrentSegmentID(self):
        """Return the selected table segment ID, falling back to Segment Editor current ID."""
        segmentationNode = self.segmentationNode()
        selectedSegmentID = self.selectedSegmentIDFromSegmentTable()
        if self.logic.isActiveSegmentID(segmentationNode, selectedSegmentID):
            return selectedSegmentID
        return self.currentSegmentID()

    def selectedSegmentIDFromSegmentTable(self):
        """Return the first selected segment ID from the embedded segment table."""
        segmentTable = self.segmentTableView()
        if not segmentTable or not hasattr(segmentTable, "selectedSegmentIDs"):
            return ""
        try:
            selectedSegmentIDs = segmentTable.selectedSegmentIDs()
        except Exception:
            logging.debug("Could not read selected segment IDs from table", exc_info=True)
            return ""
        return str(selectedSegmentIDs[0]) if selectedSegmentIDs else ""

    def segmentTableView(self):
        """Return the embedded qMRMLSegmentsTableView when it can be found."""
        if not self.segmentEditorWidget:
            return None
        for child in self.segmentEditorWidget.findChildren(qt.QWidget):
            try:
                if child.metaObject().className() == "qMRMLSegmentsTableView":
                    return child
            except Exception:
                continue
        return None

    def updateSegmentCountAndButtons(self):
        """Refresh count text and enable or disable buttons based on current selection."""
        segmentationNode = self.segmentationNode()
        count = self.logic.activeSegmentCount(segmentationNode)
        deletedCount = self.logic.logicallyDeletedSegmentCount(segmentationNode)
        self.countLabel.setText("Mucus plug count: {}".format(count))

        hasSegments = count > 0
        hasCurrentSegment = self.logic.isActiveSegmentID(
            segmentationNode,
            self.currentSegmentID(),
        )
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
        """Clear stale measurement labels for the current segment."""
        self.volumeLabel.setText("Volume: not calculated")
        self.lengthLabel.setText("Length: not calculated")
        self.ctValueLabel.setText("Median CT: not calculated")

    def updateSourceVoxelSpacing(self):
        """Show selected source CT voxel spacing in millimeters."""
        self.spacingLabel.setText(
            "Voxel spacing: {}".format(
                self.logic.volumeSpacingText(self.sourceVolumeNode())
            )
        )

    def _selectFirstSegmentIfNeeded(self):
        """Select the first active segment when the current one is invalid."""
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
            for eventName in (
                "SegmentAdded",
                "SegmentRemoved",
                "SegmentModified",
                "SegmentsOrderModified",
            ):
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
        """Keep the custom visibility button synced with Slicer's display node."""
        segmentationNode.CreateDefaultDisplayNodes()
        displayNode = segmentationNode.GetDisplayNode()
        if not displayNode:
            return
        self._observedDisplayNode = displayNode
        tag = displayNode.AddObserver(
            vtk.vtkCommand.ModifiedEvent,
            self.onObservedSegmentationDisplayChanged,
        )
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
        startTime = time.time()
        isVisible = self.logic.isSegmentationVisible(self.segmentationNode())
        if self._suppressSegmentationDisplayRefresh:
            self.debugSegmentLabelMessage(
                "Segmentation display observer: ignored during Hide/Show toggle"
            )
            return
        self.debugSegmentLabelMessage(
            "Segmentation display observer: event={} visible={}".format(
                event,
                "true" if isVisible else "false",
            )
        )
        self.updateSegmentCountAndButtons()
        if isVisible:
            labelStartTime = time.time()
            self.refreshVisibleSegmentNameLabels()
            self.debugSegmentLabelMessage(
                "Segmentation display observer: label refresh took {:.3f}s".format(
                    time.time() - labelStartTime
                )
            )
        else:
            labelStartTime = time.time()
            self._hideCurrentSegmentNameLabel()
            self.debugSegmentLabelMessage(
                "Segmentation display observer: hiding labels took {:.3f}s".format(
                    time.time() - labelStartTime
                )
            )
        self.debugSegmentLabelMessage(
            "Segmentation display observer: total took {:.3f}s".format(
                time.time() - startTime
            )
        )

    def _observeSliceNodes(self):
        """Observe slice offset/orientation changes so visible labels follow scrolling."""
        self._removeSliceNodeObservers()
        for sliceNode in slicer.util.getNodesByClass("vtkMRMLSliceNode"):
            for event in self._sliceNodeObserverEvents():
                tag = sliceNode.AddObserver(event, self.onSliceNodeModified)
                self._observedSliceNodes.append(sliceNode)
                self._sliceNodeObserverTags.append(tag)

    def _sliceNodeObserverEvents(self):
        """Return slice-node event IDs that may change which pixels are displayed."""
        events = [vtk.vtkCommand.ModifiedEvent]
        sliceNodeClass = getattr(slicer, "vtkMRMLSliceNode", None)
        for eventName in ("SliceToRASModifiedEvent", "FieldOfViewModifiedEvent"):
            event = getattr(sliceNodeClass, eventName, None) if sliceNodeClass else None
            if event is not None:
                events.append(event)
        return list(dict.fromkeys(events))

    def _observeSliceViews(self):
        """Install wheel-event filters on slice views so mouse scrolling refreshes labels."""
        if not self._sliceViewEventFiltersSupported:
            self.debugSegmentLabelMessage(
                "slice wheel filter: skipped because this Slicer build rejected it"
            )
            return
        self._removeSliceViewEventFilters()
        layoutManager = slicer.app.layoutManager()
        if not layoutManager:
            self.debugSegmentLabelMessage("slice wheel filter: no layout manager")
            return
        installedCount = 0
        for sliceViewName in layoutManager.sliceViewNames():
            sliceWidget = layoutManager.sliceWidget(sliceViewName)
            sliceView = sliceWidget.sliceView() if sliceWidget else None
            if not sliceView:
                continue
            installedCount += self._installSliceViewEventFilter(sliceView)
            for childWidget in sliceView.findChildren(qt.QWidget):
                installedCount += self._installSliceViewEventFilter(childWidget)
        self.debugSegmentLabelMessage(
            "slice wheel filter: installed on {} widget(s)".format(installedCount)
        )

    def _installSliceViewEventFilter(self, widget):
        """Install this widget as an event filter once."""
        if not self._sliceViewEventFiltersSupported:
            return 0
        if not widget or widget in self._observedSliceViews:
            return 0
        try:
            widget.installEventFilter(self)
        except Exception as exc:
            self._sliceViewEventFiltersSupported = False
            self.debugSegmentLabelMessage(
                "slice wheel filter: disabled after install failed: {}".format(exc)
            )
            return 0
        self._observedSliceViews.append(widget)
        return 1

    def _removeSliceViewEventFilters(self):
        """Remove wheel-event filters from previously observed slice views."""
        for sliceView in self._observedSliceViews:
            try:
                sliceView.removeEventFilter(self)
            except Exception:
                logging.debug("Could not remove slice view event filter", exc_info=True)
        self._observedSliceViews = []

    def eventFilter(self, watched, event):
        """Refresh labels after mouse-wheel scrolling changes a slice view."""
        try:
            if event.type() == qt.QEvent.Wheel and watched in self._observedSliceViews:
                self.debugSegmentLabelMessage(
                    "mouse wheel event caught on {}".format(
                        self.widgetDebugName(watched)
                    )
                )
                qt.QTimer.singleShot(0, self.onSliceViewWheelScrolled)
        except Exception:
            logging.debug("Could not handle slice view wheel event", exc_info=True)
        return False

    def onSliceViewWheelScrolled(self):
        """Run the complete label check after one mouse-wheel scroll step."""
        if not self._moduleIsActive or self._suppressSliceLabelRefresh:
            self.debugSegmentLabelMessage(
                "mouse wheel callback ignored: active={} suppressed={}".format(
                    self._moduleIsActive,
                    self._suppressSliceLabelRefresh,
                )
            )
            return
        self.debugSegmentLabelMessage("Mouse wheel: running all-view fallback check")
        self.refreshVisibleSegmentNameLabelsAfterSliceChange(
            reason="Mouse wheel all-view fallback check",
        )

    def _startSliceChangePolling(self):
        """Start polling slice state as a fallback for missed scroll events."""
        if not self.sliceChangePollTimer:
            return
        if self.sliceChangePollTimer.isActive():
            return
        self._lastSliceStateSignature = self.currentSliceStateSignature()
        self.sliceChangePollTimer.start()
        self.debugSegmentLabelMessage(
            "slice polling: started with interval {} ms; offsets={}".format(
                SLICE_CHANGE_POLL_INTERVAL_MS,
                self.currentSliceOffsetSummary(),
            )
        )

    def ensureSliceChangeMonitoringActive(self):
        """Start slice observers and polling when this module is visible after reload."""
        if not self.sliceChangePollTimer:
            return
        if not self._moduleIsActive and self._isModuleActiveForShortcut():
            self._moduleIsActive = True
            self.debugSegmentLabelMessage(
                "slice monitoring: activated outside enter()"
            )
        if not self._observedSliceNodes:
            self._observeSliceNodes()
        if self._sliceViewEventFiltersSupported and not self._observedSliceViews:
            self._observeSliceViews()
        if not self.sliceChangePollTimer.isActive():
            self._startSliceChangePolling()

    def _stopSliceChangePolling(self):
        """Stop polling slice state."""
        if self.sliceChangePollTimer:
            self.sliceChangePollTimer.stop()
        self._lastSliceStateSignature = None

    def onSliceChangePollTimer(self):
        """Detect silent slice changes and run the full label refresh."""
        if self._suppressSliceLabelRefresh:
            return
        if self._sliceLabelRefreshInProgress:
            return
        currentSignature = self.currentSliceStateSignature()
        if currentSignature == self._lastSliceStateSignature:
            return
        previousSignature = self._lastSliceStateSignature
        previousOffsets = self.sliceOffsetSummaryFromSignature(
            previousSignature
        )
        self._lastSliceStateSignature = currentSignature
        changedSliceNodes = self.changedSliceNodesFromSignatures(
            previousSignature,
            currentSignature,
        )
        self.debugSegmentLabelMessage(
            "slice polling: offset changed {} -> {}; changed views={}".format(
                previousOffsets,
                self.currentSliceOffsetSummary(),
                self.sliceNodeNames(changedSliceNodes),
            )
        )
        if len(changedSliceNodes) == 1:
            sliceName = self.sliceNodeNames(changedSliceNodes)
            self.refreshVisibleSegmentNameLabelsForSliceNode(
                changedSliceNodes[0],
                reason="Scroll {} polling single-view check".format(sliceName),
            )
        else:
            self.refreshVisibleSegmentNameLabelsForSliceNodes(
                changedSliceNodes if changedSliceNodes else self.currentSliceNodes(),
                reason="Scroll polling multi-view check",
            )

    def currentSliceStateSignature(self):
        """Return a compact signature of current slice matrices and field-of-view."""
        signature = []
        for sliceNode in self.currentSliceNodes():
            sliceToRAS = sliceNode.GetSliceToRAS() if sliceNode else None
            matrixValues = []
            if sliceToRAS:
                matrixValues = [
                    round(float(sliceToRAS.GetElement(row, column)), 3)
                    for row in range(3)
                    for column in range(4)
                ]
            try:
                fieldOfView = [
                    round(float(value), 3)
                    for value in sliceNode.GetFieldOfView()
                ]
            except Exception:
                fieldOfView = []
            try:
                sliceOffset = round(float(sliceNode.GetSliceOffset()), 3)
            except Exception:
                sliceOffset = None
            signature.append(
                (
                    sliceNode.GetID() if sliceNode else "",
                    sliceOffset,
                    tuple(matrixValues),
                    tuple(fieldOfView),
                )
            )
        return tuple(signature)

    def currentSliceOffsetSummary(self):
        """Return current slice offsets for debug messages."""
        return self.sliceOffsetSummaryFromSignature(self.currentSliceStateSignature())

    def changedSliceNodesFromSignatures(self, previousSignature, currentSignature):
        """Return slice nodes whose signature entry changed."""
        if not previousSignature or not currentSignature:
            return self.currentSliceNodes()
        previousByID = {item[0]: item for item in previousSignature}
        changedSliceNodes = []
        for currentItem in currentSignature:
            sliceNodeID = currentItem[0]
            if previousByID.get(sliceNodeID) == currentItem:
                continue
            sliceNode = slicer.mrmlScene.GetNodeByID(sliceNodeID)
            if sliceNode:
                changedSliceNodes.append(sliceNode)
        return changedSliceNodes

    def sliceNodeNames(self, sliceNodes):
        """Return a readable list of slice-node names for debug messages."""
        if not sliceNodes:
            return "none"
        names = []
        for sliceNode in sliceNodes:
            if sliceNode and hasattr(sliceNode, "GetName"):
                names.append(sliceNode.GetName())
            elif sliceNode:
                names.append(str(sliceNode))
        return ", ".join(names)

    def sliceOffsetSummaryFromSignature(self, signature):
        """Return a readable slice-offset summary from a slice state signature."""
        if not signature:
            return "none"
        return ", ".join(
            "{}={}".format(item[0], item[1])
            for item in signature
        )

    def widgetDebugName(self, widget):
        """Return a compact widget name for console diagnostics."""
        try:
            className = widget.metaObject().className()
        except Exception:
            className = widget.__class__.__name__
        try:
            objectName = widget.objectName
            if callable(objectName):
                objectName = objectName()
        except Exception:
            objectName = ""
        return "{}('{}')".format(className, objectName)

    def _removeSliceNodeObservers(self):
        """Remove VTK observers from slice nodes."""
        for sliceNode, tag in zip(self._observedSliceNodes, self._sliceNodeObserverTags):
            try:
                sliceNode.RemoveObserver(tag)
            except Exception:
                logging.debug("Could not remove slice node observer", exc_info=True)
        self._observedSliceNodes = []
        self._sliceNodeObserverTags = []

    def onSliceNodeModified(self, caller=None, event=None):
        """Schedule a label refresh after the user scrolls or changes a slice view."""
        if (
            not self._moduleIsActive
            or not self.labelUpdateTimer
            or self._suppressSliceLabelRefresh
        ):
            self.debugSegmentLabelMessage(
                "slice node event ignored: active={} timer={} suppressed={}".format(
                    self._moduleIsActive,
                    bool(self.labelUpdateTimer),
                    self._suppressSliceLabelRefresh,
                )
            )
            return
        callerName = caller.GetName() if caller and hasattr(caller, "GetName") else ""
        self.debugSegmentLabelMessage(
            "slice node event caught: caller={} event={}".format(callerName, event)
        )
        if caller and hasattr(caller, "GetSliceToRAS"):
            self.refreshVisibleSegmentNameLabelsForSliceNode(
                caller,
                reason="Scroll {} single-view check".format(callerName),
            )
        else:
            self.refreshVisibleSegmentNameLabelsAfterSliceChange(
                reason="Slice node all-view fallback check",
            )


#
# MucusPlugNavigatorLogic
#


class MucusPlugNavigatorLogic(ScriptedLoadableModuleLogic):
    """Keep non-UI calculations and MRML operations separate from the widget."""

    def __init__(self):
        """Create logic state used by measurement, navigation, and label helpers."""
        ScriptedLoadableModuleLogic.__init__(self)
        self._segmentLabelPointCache = {}
        self._segmentLabelCacheBuildPending = False
        self._lastSegmentLabelCacheWasBuilt = False
        self._lastSegmentLabelDebugSummary = ""

    def runDummyMucusModelTest(self, pythonExecutable=None, caseID="dummy_case"):
        """Run the bundled dummy mucus model script as an external process."""
        scriptPath = self.dummyMucusModelScriptPath()
        pythonExecutable = (
            pythonExecutable
            if pythonExecutable
            else self.defaultPythonExecutableForExternalScripts()
        )
        command = [
            pythonExecutable,
            scriptPath,
            "--case-id",
            caseID,
        ]
        return self.runExternalProcess(command)

    def dummyMucusModelScriptPath(self):
        """Return the absolute path to the bundled dummy model script."""
        return os.path.join(os.path.dirname(__file__), DUMMY_MODEL_SCRIPT_NAME)

    def defaultPythonExecutableForExternalScripts(self):
        """Return the best Python executable for launching helper scripts."""
        try:
            slicerApplicationPath = slicer.app.applicationFilePath()
            slicerApplicationFolder = os.path.dirname(slicerApplicationPath)
            pythonSlicerName = "PythonSlicer.exe" if os.name == "nt" else "PythonSlicer"
            pythonSlicerPath = os.path.join(slicerApplicationFolder, pythonSlicerName)
            if os.path.exists(pythonSlicerPath):
                return pythonSlicerPath
        except Exception:
            logging.debug("Could not find PythonSlicer executable", exc_info=True)
        return sys.executable

    def runExternalProcess(self, command):
        """Run an external command and return stdout, stderr, and exit code."""
        completedProcess = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "command": command,
            "returnCode": completedProcess.returncode,
            "stdout": completedProcess.stdout,
            "stderr": completedProcess.stderr,
        }

    def segmentIDs(self, segmentationNode):
        """Return segment IDs in the same order used by the segmentation node."""
        if not segmentationNode:
            return []
        segmentation = segmentationNode.GetSegmentation()
        if not segmentation:
            return []
        return [
            segmentation.GetNthSegmentID(index)
            for index in range(segmentation.GetNumberOfSegments())
        ]

    def segmentCount(self, segmentationNode):
        """Return the number of mucus plug segments in the selected segmentation."""
        return len(self.segmentIDs(segmentationNode))

    def markSegmentDone(self, segmentationNode, segmentID):
        """Mark one segment as completed in Slicer's segment status column."""
        segment = self.segment(segmentationNode, segmentID)
        if not segment:
            return False

        try:
            segment.SetTag(SEGMENT_STATUS_TAG_NAME, SEGMENT_STATUS_DONE_VALUE)
            if hasattr(segment, "Modified"):
                segment.Modified()
            segmentation = segmentationNode.GetSegmentation() if segmentationNode else None
            if segmentation and hasattr(segmentation, "Modified"):
                segmentation.Modified()
            segmentationNode.Modified()
            return True
        except Exception:
            logging.debug("Could not mark segment as done", exc_info=True)
            return False

    def segmentStatusTagNames(self, segment=None):
        """Return possible tag names Slicer may use for the segment status flag."""
        tagNames = []
        tagOwnerCandidates = [
            getattr(slicer, "vtkSegment", None),
            segment,
        ]
        for tagOwner in tagOwnerCandidates:
            if not tagOwner:
                continue
            for methodName in ("GetStatusTagName", "GetSegmentStatusTagName"):
                if hasattr(tagOwner, methodName):
                    try:
                        tagNames.append(getattr(tagOwner, methodName)())
                    except Exception:
                        logging.debug("Could not read segment status tag name", exc_info=True)
        tagNames.append(SEGMENT_STATUS_TAG_NAME)
        return list(dict.fromkeys([tagName for tagName in tagNames if tagName]))

    def segmentStatusDebugRows(self, segmentationNode):
        """Return current status tag values for all segments for Slicer-console debugging."""
        rows = []
        segmentation = segmentationNode.GetSegmentation() if segmentationNode else None
        if not segmentation:
            return rows

        for segmentID in self.segmentIDs(segmentationNode):
            segment = segmentation.GetSegment(segmentID)
            row = {
                "segmentID": segmentID,
                "segmentName": segment.GetName() if segment else segmentID,
                "statusTags": {},
            }
            for tagName in self.segmentStatusTagNames(segment):
                tagValue = self.segmentTagValue(segment, tagName)
                if tagValue not in (None, ""):
                    row["statusTags"][tagName] = tagValue
            rows.append(row)
        return rows

    def segmentTagValue(self, segment, tagName):
        """Read one segment tag value across Slicer Python wrapping variants."""
        if not segment or not tagName:
            return None
        try:
            return segment.GetTag(tagName)
        except TypeError:
            pass
        except Exception:
            logging.debug("Could not read segment tag value", exc_info=True)
            return None

        try:
            tagValue = ""
            if segment.GetTag(tagName, tagValue):
                return tagValue
        except Exception:
            logging.debug("Could not read segment tag value by reference", exc_info=True)
        return None

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
        """Move selected deleted segments back to the active segmentation."""
        deletedSegmentIDs = self.logicallyDeletedSegmentIDs(segmentationNode)
        if segmentIDs is None:
            restoredSegmentIDs = deletedSegmentIDs
        else:
            requestedSegmentIDs = set(segmentIDs)
            restoredSegmentIDs = [
                segmentID
                for segmentID in deletedSegmentIDs
                if segmentID in requestedSegmentIDs
            ]
        backupNode = self.deletedBackupNode(segmentationNode, create=False)
        if not backupNode:
            return []
        actuallyRestoredSegmentIDs = []
        for segmentID in restoredSegmentIDs:
            restoredSegmentID = self.restoreSegmentFromBackup(
                backupNode,
                segmentationNode,
                segmentID,
            )
            if restoredSegmentID:
                backupNode.GetSegmentation().RemoveSegment(segmentID)
                backupNode.SetAttribute(
                    self.deletedSegmentColorAttributeName(segmentID),
                    None,
                )
                backupNode.Modified()
                self.renameSegmentIfNameConflictsWithDeletedBackup(
                    segmentationNode,
                    restoredSegmentID,
                )
                actuallyRestoredSegmentIDs.append(restoredSegmentID)
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
        """Copy a segment to the backup node and remove it from the active list."""
        backupNode = self.deletedBackupNode(segmentationNode, create=True)
        if not backupNode:
            return False
        self.storeDeletedSegmentColor(segmentationNode, backupNode, segmentID)
        if not self.copySegmentBetweenSegmentations(segmentationNode, backupNode, segmentID):
            return False
        segmentationNode.GetSegmentation().RemoveSegment(segmentID)
        segmentationNode.Modified()
        return True

    def restoreSegmentFromBackup(self, backupNode, segmentationNode, segmentID):
        """Restore one backup segment, using a new ID if the old ID was reused."""
        if not self.isValidSegmentID(backupNode, segmentID):
            return ""
        targetSegmentation = segmentationNode.GetSegmentation() if segmentationNode else None
        if not targetSegmentation:
            return ""

        restoredSegmentID = segmentID
        if targetSegmentation.GetSegment(restoredSegmentID):
            restoredSegmentID = self.uniqueSegmentID(segmentationNode, segmentID)

        if self.copySegmentBetweenSegmentations(
            backupNode,
            segmentationNode,
            segmentID,
            restoredSegmentID,
        ):
            return restoredSegmentID
        return ""

    def copySegmentBetweenSegmentations(
        self,
        sourceSegmentationNode,
        targetSegmentationNode,
        segmentID,
        targetSegmentID=None,
    ):
        """Copy one segment between segmentation nodes."""
        if (
            not self.isValidSegmentID(sourceSegmentationNode, segmentID)
            or not targetSegmentationNode
        ):
            return False
        sourceSegmentation = sourceSegmentationNode.GetSegmentation()
        targetSegmentation = targetSegmentationNode.GetSegmentation()
        if not sourceSegmentation or not targetSegmentation:
            return False
        targetSegmentID = targetSegmentID if targetSegmentID else segmentID
        if targetSegmentation.GetSegment(targetSegmentID):
            return True
        try:
            segmentCopy = slicer.vtkSegment()
            segmentCopy.DeepCopy(sourceSegmentation.GetSegment(segmentID))
            targetSegmentation.AddSegment(segmentCopy, targetSegmentID)
            targetSegmentationNode.Modified()
            return targetSegmentation.GetSegment(targetSegmentID) is not None
        except Exception:
            logging.debug(
                "vtkSegment.DeepCopy failed; trying CopySegmentFromSegmentation",
                exc_info=True,
            )
        try:
            if targetSegmentID != segmentID and targetSegmentation.GetSegment(segmentID):
                logging.debug(
                    "Copy fallback skipped because the original target ID is already used: %s",
                    segmentID,
                )
                return False
            targetSegmentation.CopySegmentFromSegmentation(
                sourceSegmentation,
                segmentID,
            )
            targetSegmentationNode.Modified()
            copiedSegment = targetSegmentation.GetSegment(segmentID)
            if copiedSegment and targetSegmentID != segmentID:
                segmentCopy = slicer.vtkSegment()
                segmentCopy.DeepCopy(copiedSegment)
                targetSegmentation.RemoveSegment(segmentID)
                targetSegmentation.AddSegment(segmentCopy, targetSegmentID)
            return targetSegmentation.GetSegment(targetSegmentID) is not None
        except Exception:
            logging.exception("Could not copy segment: %s", segmentID)
            return False

    def renameSegmentIfNameConflictsWithDeletedBackup(self, segmentationNode, segmentID):
        """Rename an active segment if its name is already used or restorable."""
        segment = self.segment(segmentationNode, segmentID)
        if not segment:
            return ""

        reservedNames = set(self.segmentNames(segmentationNode, excludeSegmentID=segmentID))
        reservedNames.update(self.deletedSegmentNames(segmentationNode))
        currentName = segment.GetName()
        if currentName not in reservedNames:
            return currentName

        uniqueName = self.nextSequentialSegmentName(currentName, reservedNames)
        segment.SetName(uniqueName)
        segmentationNode.Modified()
        return uniqueName

    def segment(self, segmentationNode, segmentID):
        """Return one segment object, or None when the ID is not valid."""
        if not segmentationNode or not segmentID:
            return None
        segmentation = segmentationNode.GetSegmentation()
        return segmentation.GetSegment(segmentID) if segmentation else None

    def segmentName(self, segmentationNode, segmentID):
        """Return a display name for a segment ID."""
        segment = self.segment(segmentationNode, segmentID)
        if segment:
            return segment.GetName()
        return segmentID

    def segmentDisplayColor(self, segmentationNode, segmentID):
        """Return the visible RGB color for one segment."""
        if segmentationNode and segmentID:
            displayNode = segmentationNode.GetDisplayNode()
            if displayNode and hasattr(displayNode, "GetSegmentOverrideColor"):
                color = [0.0, 0.0, 0.0]
                try:
                    if displayNode.GetSegmentOverrideColor(segmentID, color):
                        return [
                            float(color[0]),
                            float(color[1]),
                            float(color[2]),
                        ]
                except Exception:
                    logging.debug("Could not read segment override color", exc_info=True)
        color = self.segmentColor(self.segment(segmentationNode, segmentID))
        return color if color else [1.0, 1.0, 1.0]

    def segmentNames(self, segmentationNode, excludeSegmentID=None):
        """Return segment names in segmentation order, optionally excluding one ID."""
        names = []
        segmentation = segmentationNode.GetSegmentation() if segmentationNode else None
        if not segmentation:
            return names
        for segmentID in self.segmentIDs(segmentationNode):
            if segmentID == excludeSegmentID:
                continue
            segment = segmentation.GetSegment(segmentID)
            if segment:
                names.append(segment.GetName())
        return names

    def deletedSegmentNames(self, segmentationNode):
        """Return names currently waiting in the restore backup list."""
        backupNode = self.deletedBackupNode(segmentationNode, create=False)
        return self.segmentNames(backupNode)

    def nextSequentialSegmentName(self, requestedName, reservedNames):
        """Return the next numbered segment name after active and restorable names."""
        match = re.match(r"^(.*?)(\d+)$", requestedName)
        if not match:
            return self.uniqueSegmentName(requestedName, reservedNames)

        prefix = match.group(1)
        highestNumber = int(match.group(2))
        for reservedName in reservedNames:
            reservedMatch = re.match(r"^{}(\d+)$".format(re.escape(prefix)), reservedName)
            if reservedMatch:
                highestNumber = max(highestNumber, int(reservedMatch.group(1)))

        while True:
            highestNumber += 1
            candidateName = "{}{}".format(prefix, highestNumber)
            if candidateName not in reservedNames:
                return candidateName

    def uniqueSegmentName(self, requestedName, reservedNames):
        """Return a fallback readable segment name that is not already reserved."""
        if requestedName not in reservedNames:
            return requestedName
        suffix = 1
        while True:
            candidateName = "{}_{}".format(requestedName, suffix)
            if candidateName not in reservedNames:
                return candidateName
            suffix += 1

    def uniqueSegmentID(self, segmentationNode, requestedSegmentID):
        """Return a segment ID that is not already used in the target segmentation."""
        usedSegmentIDs = set(self.segmentIDs(segmentationNode))
        if requestedSegmentID not in usedSegmentIDs:
            return requestedSegmentID
        suffix = 1
        while True:
            candidateSegmentID = "{}_restored_{}".format(requestedSegmentID, suffix)
            if candidateSegmentID not in usedSegmentIDs:
                return candidateSegmentID
            suffix += 1

    def deletedBackupNode(self, segmentationNode, create=False):
        """Return the hidden backup node for logically deleted segments."""
        if not segmentationNode:
            return None
        backupNode = None
        backupNodeID = segmentationNode.GetAttribute(DELETED_BACKUP_NODE_ATTRIBUTE)
        if backupNodeID:
            backupNode = slicer.mrmlScene.GetNodeByID(backupNodeID)
        if backupNode or not create:
            return backupNode

        backupNodeName = "{} deleted mucus backup".format(segmentationNode.GetName())
        backupNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode",
            backupNodeName,
        )
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
                logging.debug(
                    "Could not hide deleted backup node from editors",
                    exc_info=True,
                )
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
        segment = (
            segmentationNode.GetSegmentation().GetSegment(segmentID)
            if segmentationNode
            else None
        )
        color = self.segmentColor(segment)
        if backupNode and color:
            backupNode.SetAttribute(
                self.deletedSegmentColorAttributeName(segmentID),
                json.dumps(color),
            )

    def deletedSegmentColor(self, backupNode, segmentID, segment=None):
        """Return the stored or segment-defined color for a deleted segment."""
        if backupNode and segmentID:
            storedValue = backupNode.GetAttribute(
                self.deletedSegmentColorAttributeName(segmentID)
            )
            if storedValue:
                try:
                    color = json.loads(storedValue)
                    if len(color) >= 3:
                        return [
                            float(color[0]),
                            float(color[1]),
                            float(color[2]),
                        ]
                except Exception:
                    logging.debug(
                        "Could not parse deleted segment color",
                        exc_info=True,
                    )
        color = self.segmentColor(segment)
        return color if color else [0.5, 0.5, 0.5]

    def segmentColor(self, segment):
        """Return a segment color as RGB values between 0 and 1."""
        if not segment:
            return None
        try:
            color = segment.GetColor()
            if color and len(color) >= 3:
                return [
                    float(color[0]),
                    float(color[1]),
                    float(color[2]),
                ]
        except Exception:
            pass
        try:
            color = [0.5, 0.5, 0.5]
            segment.GetColor(color)
            return [
                float(color[0]),
                float(color[1]),
                float(color[2]),
            ]
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
            logging.debug(
                "Could not parse legacy logical delete segment list",
                exc_info=True,
            )
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
            if (
                segmentID in validSegmentIDs
                and segmentID not in orderedUniqueSegmentIDs
            ):
                orderedUniqueSegmentIDs.append(segmentID)
        if orderedUniqueSegmentIDs:
            segmentationNode.SetAttribute(
                LOGICALLY_DELETED_SEGMENTS_ATTRIBUTE,
                json.dumps(orderedUniqueSegmentIDs),
            )
        else:
            segmentationNode.SetAttribute(LOGICALLY_DELETED_SEGMENTS_ATTRIBUTE, None)
        segmentationNode.Modified()

    def mucusPlugMeasurementRows(
        self,
        segmentationNode,
        referenceVolumeNode=None,
        skipLengthAbovePixels=None,
        includeMedianCTValue=False,
    ):
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
                includeMedianCTValue,
            )
            rows.append(
                {
                    "segmentID": segmentID,
                    "segmentName": segmentName,
                    "volumePixels": metrics["volumePixels"] if metrics else "",
                    "volumeMm3Text": (
                        metrics["volumeMm3Text"] if metrics else ""
                    ),
                    "lengthPixels": metrics["lengthPixels"] if metrics else "",
                    "lengthMmText": metrics["lengthMmText"] if metrics else "",
                    "medianCTValueText": (
                        metrics["medianCTValueText"] if metrics else ""
                    ),
                }
            )
        return rows

    def exportMucusPlugMeasurementRows(self, segmentationNode, referenceVolumeNode=None):
        """Return export rows after removing large mask-like segments from the CSV output."""
        rows = self.mucusPlugMeasurementRows(
            segmentationNode,
            referenceVolumeNode,
            skipLengthAbovePixels=EXPORT_MASK_MIN_VOLUME_PIXELS,
            includeMedianCTValue=True,
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
        """Return True when a row looks like a whole-mask segment."""
        try:
            return int(row["volumePixels"]) >= EXPORT_MASK_MIN_VOLUME_PIXELS
        except Exception:
            return False

    def segmentVoxelMetrics(
        self,
        segmentationNode,
        segmentID,
        referenceVolumeNode=None,
        skipLengthAbovePixels=None,
        includeMedianCTValue=False,
    ):
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
            volumeMm3Text = self.physicalVolumeMm3Text(
                volumePixels,
                referenceVolumeNode,
            )
        except Exception:
            logging.exception(
                "Could not compute segment voxel measurements for segment: %s",
                segmentID,
            )
            return None

        if volumePixels == 0:
            return {
                "volumePixels": 0,
                "volumeMm3Text": volumeMm3Text,
                "lengthPixels": 0,
                "lengthMmText": "not available",
                "medianCTValueText": "not available",
            }
        if (
            skipLengthAbovePixels is not None
            and volumePixels >= skipLengthAbovePixels
        ):
            return {
                "volumePixels": volumePixels,
                "volumeMm3Text": volumeMm3Text,
                "lengthPixels": "",
                "lengthMmText": "",
                "medianCTValueText": "",
            }

        medianCTValueText = ""
        if includeMedianCTValue:
            medianCTValueText = self.segmentMedianCTValueText(
                segmentationNode,
                segmentID,
                referenceVolumeNode,
                np,
            )

        if volumePixels == 1:
            return {
                "volumePixels": volumePixels,
                "volumeMm3Text": volumeMm3Text,
                "lengthPixels": 1,
                "lengthMmText": self.singleVoxelLengthMmText(referenceVolumeNode),
                "medianCTValueText": medianCTValueText,
            }

        occupiedVoxelCoordinates = np.argwhere(occupiedMask)
        lengthPixels = self._principalAxisLengthPixels(occupiedVoxelCoordinates, np)
        lengthMmText = self._principalAxisLengthMmText(
            occupiedVoxelCoordinates,
            referenceVolumeNode,
            np,
        )
        return {
            "volumePixels": volumePixels,
            "volumeMm3Text": volumeMm3Text,
            "lengthPixels": max(lengthPixels, 1),
            "lengthMmText": lengthMmText,
            "medianCTValueText": medianCTValueText,
        }

    def physicalVolumeMm3Text(self, volumePixels, volumeNode):
        """Return physical volume in mm3 using source voxel spacing."""
        spacing = self.volumeSpacingValues(volumeNode)
        if not spacing:
            return "not available"
        voxelVolumeMm3 = spacing[0] * spacing[1] * spacing[2]
        return self.formatPhysicalMeasurement(volumePixels * voxelVolumeMm3)

    def singleVoxelLengthMmText(self, volumeNode):
        """Return a one-voxel length estimate in millimeters."""
        spacing = self.volumeSpacingValues(volumeNode)
        if not spacing:
            return "not available"
        return self.formatPhysicalMeasurement(max(spacing))

    def volumeSpacingText(self, volumeNode):
        """Return source volume voxel spacing as readable millimeter text."""
        values = self.volumeSpacingTextValues(volumeNode)
        if not values:
            return "not available"
        return "{} x {} x {} mm".format(values[0], values[1], values[2])

    def volumeSpacingTextValues(self, volumeNode):
        """Return source volume spacing values formatted for UI and CSV."""
        spacing = self.volumeSpacingValues(volumeNode)
        if not spacing:
            return []
        return [self.formatScalarValue(value) for value in spacing]

    def volumeSpacingValues(self, volumeNode):
        """Return source volume spacing as numeric X, Y, Z millimeter values."""
        if not volumeNode:
            return None
        try:
            spacing = volumeNode.GetSpacing()
            return [float(spacing[index]) for index in range(3)]
        except Exception:
            logging.debug("Could not read source volume spacing", exc_info=True)
            return None

    def segmentMedianCTValueText(
        self,
        segmentationNode,
        segmentID,
        sourceVolumeNode,
        np,
    ):
        """Return the median source CT scalar value inside one segment."""
        if not sourceVolumeNode:
            return "not available"
        try:
            segmentArray = self._segmentArrayInReferenceGeometry(
                segmentationNode,
                segmentID,
                sourceVolumeNode,
            )
            sourceArray = slicer.util.arrayFromVolume(sourceVolumeNode)
            if segmentArray.shape != sourceArray.shape:
                logging.debug(
                    "Segment mask shape does not match source volume shape"
                )
                return "not available"
            segmentMask = segmentArray != 0
            if not np.any(segmentMask):
                return "not available"
            medianValue = np.median(sourceArray[segmentMask])
            return self.formatScalarValue(medianValue)
        except Exception:
            logging.debug("Could not calculate median CT value", exc_info=True)
            return "not available"

    def formatScalarValue(self, value):
        """Format a CT scalar value like Slicer's Data Probe display."""
        roundedValue = round(float(value))
        if abs(float(value) - roundedValue) < 1e-6:
            return str(int(roundedValue))
        return "{:.3f}".format(float(value))

    def formatPhysicalMeasurement(self, value):
        """Format a physical measurement without unnecessary trailing zeros."""
        return "{:.3f}".format(float(value)).rstrip("0").rstrip(".")

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

    def _segmentArrayInReferenceGeometry(
        self,
        segmentationNode,
        segmentID,
        referenceVolumeNode,
    ):
        """Return a segment labelmap resampled into the source-volume geometry."""
        if not referenceVolumeNode:
            return self._segmentArray(segmentationNode, segmentID, referenceVolumeNode)
        return slicer.util.arrayFromSegmentBinaryLabelmap(
            segmentationNode,
            segmentID,
            referenceVolumeNode,
        )

    def _occupiedVoxelCoordinates(
        self,
        segmentationNode,
        segmentID,
        referenceVolumeNode,
        np,
        forceReferenceGeometry=False,
    ):
        """Convert a segment labelmap into nonzero voxel coordinates."""
        if forceReferenceGeometry:
            segmentArray = self._segmentArrayInReferenceGeometry(
                segmentationNode,
                segmentID,
                referenceVolumeNode,
            )
        else:
            segmentArray = self._segmentArray(
                segmentationNode,
                segmentID,
                referenceVolumeNode,
            )
        return np.argwhere(segmentArray != 0)

    def _principalAxisLengthPixels(self, occupiedVoxelCoordinates, np):
        """Estimate segment length in pixels along the principal component axis."""
        centeredCoordinates = (
            occupiedVoxelCoordinates - occupiedVoxelCoordinates.mean(axis=0)
        )
        try:
            _, _, principalAxes = np.linalg.svd(centeredCoordinates, full_matrices=False)
            principalAxis = principalAxes[0]
            projectedCoordinates = occupiedVoxelCoordinates.dot(principalAxis)
            return int(round(projectedCoordinates.max() - projectedCoordinates.min() + 1))
        except Exception:
            voxelDimensions = (
                occupiedVoxelCoordinates.max(axis=0)
                - occupiedVoxelCoordinates.min(axis=0)
                + 1
            )
            return int(voxelDimensions.max())

    def _principalAxisLengthMmText(
        self,
        occupiedVoxelCoordinates,
        volumeNode,
        np,
    ):
        """Estimate segment length in millimeters along the principal axis."""
        spacingKji = self.volumeSpacingKjiValues(volumeNode)
        if not spacingKji:
            return "not available"

        physicalCoordinates = occupiedVoxelCoordinates * np.array(spacingKji)
        centeredCoordinates = (
            physicalCoordinates - physicalCoordinates.mean(axis=0)
        )
        try:
            _, _, principalAxes = np.linalg.svd(
                centeredCoordinates,
                full_matrices=False,
            )
            principalAxis = principalAxes[0]
            projectedCoordinates = physicalCoordinates.dot(principalAxis)
            voxelWidthMm = np.abs(principalAxis).dot(np.array(spacingKji))
            lengthMm = (
                projectedCoordinates.max()
                - projectedCoordinates.min()
                + voxelWidthMm
            )
            return self.formatPhysicalMeasurement(lengthMm)
        except Exception:
            voxelDimensions = (
                occupiedVoxelCoordinates.max(axis=0)
                - occupiedVoxelCoordinates.min(axis=0)
                + 1
            )
            lengthMm = (voxelDimensions * np.array(spacingKji)).max()
            return self.formatPhysicalMeasurement(lengthMm)

    def volumeSpacingKjiValues(self, volumeNode):
        """Return source spacing in NumPy array coordinate order: K, J, I."""
        spacing = self.volumeSpacingValues(volumeNode)
        if not spacing:
            return None
        return [spacing[2], spacing[1], spacing[0]]

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
            logging.warning(
                "Could not find center for segment '%s' (ID: %s). The segment may be empty.",
                self.segmentName(segmentationNode, segmentID),
                segmentID,
            )
            return False

        self.ensureSliceFieldOfViewBaseline(baseFieldOfViewBySliceNodeID)
        for sliceNode in slicer.util.getNodesByClass("vtkMRMLSliceNode"):
            sliceNode.JumpSliceByCentering(centerRAS[0], centerRAS[1], centerRAS[2])

        self.applySliceZoom(zoomFactor, baseFieldOfViewBySliceNodeID)
        return True

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

    def visibleSegmentLabelEntries(
        self,
        segmentationNode,
        sliceNodes,
        referenceVolumeNode=None,
        allowCacheBuild=True,
        cacheBuildLimit=SEGMENT_NAME_LABEL_CACHE_BUILD_LIMIT,
    ):
        """Return label data for active segments intersecting current slice views."""
        labelEntries = []
        if not segmentationNode or not sliceNodes:
            self._lastSegmentLabelDebugSummary = "no segmentation or slice nodes"
            return labelEntries
        self._segmentLabelCacheBuildPending = False
        uncachedBuildsRemaining = (
            float("inf") if cacheBuildLimit is None else cacheBuildLimit
        )
        debugCounts = {
            "active": 0,
            "visible": 0,
            "bounds": 0,
            "checked": 0,
            "cacheBuilt": 0,
            "cachePending": 0,
            "labels": 0,
            "noPixel": 0,
        }

        for segmentID in self.activeSegmentIDs(segmentationNode):
            debugCounts["active"] += 1
            if not self.isSegmentVisible(segmentationNode, segmentID):
                continue
            debugCounts["visible"] += 1
            if not self.segmentBoundsIntersectAnySliceView(
                segmentationNode,
                segmentID,
                sliceNodes,
            ):
                continue
            debugCounts["bounds"] += 1
            labelPositionsRAS = self.segmentLabelPositionsFromPixelsOnSlicesRAS(
                segmentationNode,
                segmentID,
                sliceNodes,
                referenceVolumeNode,
                allowCacheBuild=(
                    allowCacheBuild and uncachedBuildsRemaining > 0
                ),
            )
            debugCounts["checked"] += 1
            if self._lastSegmentLabelCacheWasBuilt:
                uncachedBuildsRemaining -= 1
                debugCounts["cacheBuilt"] += 1
            if self._segmentLabelCacheBuildPending:
                debugCounts["cachePending"] += 1
            if not labelPositionsRAS and not referenceVolumeNode:
                labelPositionsRAS = self.segmentLabelPositionsOnSlicesRAS(
                    segmentationNode,
                    segmentID,
                    sliceNodes,
                )
            if not labelPositionsRAS:
                debugCounts["noPixel"] += 1
                continue
            segmentName = self.segmentName(segmentationNode, segmentID)
            segmentColor = self.segmentDisplayColor(segmentationNode, segmentID)
            for positionIndex, labelPosition in enumerate(labelPositionsRAS):
                viewNodeID = labelPosition.get("viewNodeID", "")
                labelEntries.append(
                    {
                        "labelID": "{}_{}".format(
                            segmentID,
                            viewNodeID if viewNodeID else "slice{}".format(positionIndex),
                        ),
                        "segmentID": segmentID,
                        "segmentName": segmentName,
                        "color": segmentColor,
                        "positionRAS": labelPosition["positionRAS"],
                        "viewNodeID": viewNodeID,
                    }
                )
                debugCounts["labels"] += 1
        self._lastSegmentLabelDebugSummary = ", ".join(
            "{}={}".format(key, value) for key, value in debugCounts.items()
        )
        return labelEntries

    def segmentLabelCacheBuildPending(self):
        """Return True when another label refresh should continue cache building."""
        return self._segmentLabelCacheBuildPending

    def lastSegmentLabelDebugSummary(self):
        """Return the latest label-check debug counter summary."""
        return self._lastSegmentLabelDebugSummary

    def segmentLabelPositionsFromPixelsOnSlicesRAS(
        self,
        segmentationNode,
        segmentID,
        sliceNodes,
        referenceVolumeNode,
        allowCacheBuild=True,
    ):
        """Return label positions for slice views that show at least one segment pixel."""
        if not referenceVolumeNode:
            return []
        try:
            import numpy as np

            pointData = self.cachedSegmentLabelPointsRAS(
                segmentationNode,
                segmentID,
                np,
                allowCacheBuild=allowCacheBuild,
            )
        except Exception:
            logging.debug(
                "Could not get segment pixels for visible label check",
                exc_info=True,
            )
            return []

        if pointData is None:
            return []

        labelPositionsRAS = []
        for sliceNode in sliceNodes:
            plane = self.slicePlaneRAS(sliceNode)
            if plane is None:
                continue
            labelPositionRAS = self.labelPositionForSlicePixelsRAS(
                pointData,
                plane,
                np,
            )
            if labelPositionRAS is not None:
                labelPositionsRAS.append(
                    {
                        "positionRAS": labelPositionRAS,
                        "viewNodeID": sliceNode.GetID() if sliceNode else "",
                    }
                )
        return labelPositionsRAS

    def cachedSegmentLabelPointsRAS(
        self,
        segmentationNode,
        segmentID,
        np,
        allowCacheBuild=True,
    ):
        """Return cached RAS point data for one segment's nonzero labelmap pixels."""
        self._lastSegmentLabelCacheWasBuilt = False
        cacheKey = self.segmentLabelPointCacheKey(segmentationNode, segmentID)
        segmentMTime = self.segmentMTime(segmentationNode, segmentID)
        cachedEntry = self._segmentLabelPointCache.get(cacheKey)
        if cachedEntry and cachedEntry.get("segmentMTime") == segmentMTime:
            return cachedEntry.get("pointData")

        if not allowCacheBuild:
            self._segmentLabelCacheBuildPending = True
            return None

        pointData = self.segmentLabelPointsRAS(segmentationNode, segmentID, np)
        self._lastSegmentLabelCacheWasBuilt = True
        self._segmentLabelPointCache[cacheKey] = {
            "segmentMTime": segmentMTime,
            "pointData": pointData,
        }
        return pointData

    def segmentLabelPointCacheKey(self, segmentationNode, segmentID):
        """Return the cache key used for one segment's label points."""
        nodeID = segmentationNode.GetID() if segmentationNode else ""
        return "{}|{}".format(nodeID, segmentID)

    def segmentMTime(self, segmentationNode, segmentID):
        """Return a segment modification time for cache invalidation."""
        segment = self.segment(segmentationNode, segmentID)
        if segment and hasattr(segment, "GetMTime"):
            try:
                return segment.GetMTime()
            except Exception:
                logging.debug("Could not read segment MTime", exc_info=True)
        return 0

    def segmentLabelPointsRAS(self, segmentationNode, segmentID, np):
        """Build RAS point data for one segment's nonzero labelmap pixels."""
        imageData = self.segmentBinaryLabelmapRepresentation(
            segmentationNode,
            segmentID,
        )
        occupiedVoxelCoordinates = self.occupiedImageVoxelCoordinates(
            imageData,
            np,
        )
        if occupiedVoxelCoordinates.size == 0:
            return None
        if occupiedVoxelCoordinates.shape[0] >= EXPORT_MASK_MIN_VOLUME_PIXELS:
            return None
        return self.imageVoxelPointDataRAS(
            occupiedVoxelCoordinates,
            imageData,
            np,
        )

    def segmentBinaryLabelmapRepresentation(self, segmentationNode, segmentID):
        """Return a cropped binary labelmap image for one segment."""
        imageData = self.createOrientedImageData()
        if not imageData:
            return None
        success = segmentationNode.GetBinaryLabelmapRepresentation(
            segmentID,
            imageData,
        )
        if success is False:
            return None
        return imageData

    def createOrientedImageData(self):
        """Create vtkOrientedImageData across Slicer Python wrapping variants."""
        if hasattr(slicer, "vtkOrientedImageData"):
            return slicer.vtkOrientedImageData()
        try:
            import vtkSegmentationCorePython as vtkSegmentationCore

            return vtkSegmentationCore.vtkOrientedImageData()
        except Exception:
            logging.debug("Could not create vtkOrientedImageData", exc_info=True)
            return None

    def occupiedImageVoxelCoordinates(self, imageData, np):
        """Return nonzero voxel coordinates from a cropped segment image."""
        if not imageData:
            return np.empty((0, 3), dtype=int)
        scalars = imageData.GetPointData().GetScalars()
        if not scalars:
            return np.empty((0, 3), dtype=int)

        from vtk.util import numpy_support

        dimensions = imageData.GetDimensions()
        if not dimensions or min(dimensions) <= 0:
            return np.empty((0, 3), dtype=int)
        segmentArray = numpy_support.vtk_to_numpy(scalars).reshape(
            dimensions[2],
            dimensions[1],
            dimensions[0],
        )
        return np.argwhere(segmentArray != 0)

    def imageVoxelPointDataRAS(self, occupiedVoxelCoordinates, imageData, np):
        """Convert cropped KJI image coordinates into RAS points and voxel axes."""
        if not imageData or occupiedVoxelCoordinates.size == 0:
            return None

        ijkCoordinates = occupiedVoxelCoordinates[:, [2, 1, 0]].astype(float)
        extent = imageData.GetExtent()
        ijkCoordinates[:, 0] += float(extent[0])
        ijkCoordinates[:, 1] += float(extent[2])
        ijkCoordinates[:, 2] += float(extent[4])

        homogeneousCoordinates = np.ones(
            (ijkCoordinates.shape[0], 4),
            dtype=float,
        )
        homogeneousCoordinates[:, 0:3] = ijkCoordinates

        imageToRAS = vtk.vtkMatrix4x4()
        imageData.GetImageToWorldMatrix(imageToRAS)
        transform = np.array(
            [
                [imageToRAS.GetElement(row, column) for column in range(4)]
                for row in range(4)
            ],
            dtype=float,
        )
        return {
            "pointsRAS": homogeneousCoordinates.dot(transform.T)[:, 0:3],
            "axisVectorsRAS": [
                transform[0:3, 0],
                transform[0:3, 1],
                transform[0:3, 2],
            ],
        }

    def segmentBoundsIntersectAnySliceView(self, segmentationNode, segmentID, sliceNodes):
        """Return True when segment bounds overlap any currently displayed slice view."""
        boundsRAS = self.segmentBoundsRAS(segmentationNode, segmentID)
        if boundsRAS is None:
            return True
        boundsCornersRAS = self.boundsCornersRAS(boundsRAS)
        for sliceNode in sliceNodes:
            plane = self.slicePlaneRAS(sliceNode)
            if plane is None:
                continue
            projectedBounds = self.projectBoundsToSlice(boundsCornersRAS, plane)
            if self.projectedBoundsIntersectSliceView(projectedBounds, plane):
                return True
        return False

    def labelPositionForSlicePixelsRAS(self, pointData, plane, np):
        """Return a label point when at least one segment pixel is visible."""
        pointsRAS = pointData.get("pointsRAS") if pointData else None
        if pointsRAS is None:
            return None
        origin = np.asarray(plane["origin"], dtype=float)
        xAxis = np.asarray(plane["xAxis"], dtype=float)
        yAxis = np.asarray(plane["yAxis"], dtype=float)
        normal = np.asarray(plane["normal"], dtype=float)
        axisVectorsRAS = pointData.get("axisVectorsRAS")
        sliceToleranceMM = self.sliceVoxelIntersectionToleranceMM(
            axisVectorsRAS, normal, np
        )
        xToleranceMM = self.sliceVoxelIntersectionToleranceMM(
            axisVectorsRAS, xAxis, np
        )
        yToleranceMM = self.sliceVoxelIntersectionToleranceMM(
            axisVectorsRAS, yAxis, np
        )

        pointsFromOrigin = pointsRAS - origin
        projectedX = pointsFromOrigin.dot(xAxis)
        projectedY = pointsFromOrigin.dot(yAxis)
        distanceFromSlice = pointsFromOrigin.dot(normal)

        visiblePixelMask = (
            (np.abs(distanceFromSlice) <= sliceToleranceMM)
            & (projectedX >= -plane["halfWidth"] - xToleranceMM)
            & (projectedX <= plane["halfWidth"] + xToleranceMM)
            & (projectedY >= -plane["halfHeight"] - yToleranceMM)
            & (projectedY <= plane["halfHeight"] + yToleranceMM)
        )
        if not np.any(visiblePixelMask):
            return None

        visibleX = projectedX[visiblePixelMask]
        visibleY = projectedY[visiblePixelMask]
        projectedBounds = {
            "minX": float(visibleX.min()),
            "maxX": float(visibleX.max()),
            "minY": float(visibleY.min()),
            "maxY": float(visibleY.max()),
        }
        margin = self.slicePixelLabelMargin(projectedBounds)
        labelX = self.labelXPositionInsideSliceView(projectedBounds, plane, margin)
        labelY = self.clamp(
            (projectedBounds["minY"] + projectedBounds["maxY"]) * 0.5,
            -plane["halfHeight"] + margin,
            plane["halfHeight"] - margin,
        )
        labelPoint = origin + xAxis * labelX + yAxis * labelY
        return [float(labelPoint[0]), float(labelPoint[1]), float(labelPoint[2])]

    def sliceVoxelIntersectionToleranceMM(self, axisVectorsRAS, normal, np):
        """Return the half-voxel distance where one pixel still intersects."""
        if not axisVectorsRAS:
            return SEGMENT_NAME_LABEL_SLICE_TOLERANCE_MM
        toleranceMM = 0.0
        for axisVector in axisVectorsRAS:
            toleranceMM += abs(float(np.dot(axisVector, normal))) * 0.5
        return max(toleranceMM + 0.01, 0.01)

    def slicePixelLabelMargin(self, projectedBounds):
        """Return a small label offset based on visible segment size in the slice."""
        visibleWidth = max(projectedBounds["maxX"] - projectedBounds["minX"], 0.0)
        visibleHeight = max(projectedBounds["maxY"] - projectedBounds["minY"], 0.0)
        return max(
            max(visibleWidth, visibleHeight) * SEGMENT_NAME_LABEL_OFFSET_FRACTION,
            SEGMENT_NAME_LABEL_OFFSET_MINIMUM_MM,
        )

    def isSegmentVisible(self, segmentationNode, segmentID):
        """Return True if one segment is visible in the segmentation display node."""
        if not self.isValidSegmentID(segmentationNode, segmentID):
            return False
        segmentationNode.CreateDefaultDisplayNodes()
        displayNode = segmentationNode.GetDisplayNode()
        if not displayNode:
            return True
        try:
            return bool(displayNode.GetSegmentVisibility(segmentID))
        except Exception:
            return True

    def segmentLabelPositionOnAnySliceRAS(self, segmentationNode, segmentID, sliceNodes):
        """Return the first label position for a segment intersecting any current slice."""
        labelPositionsRAS = self.segmentLabelPositionsOnSlicesRAS(
            segmentationNode,
            segmentID,
            sliceNodes,
        )
        return labelPositionsRAS[0]["positionRAS"] if labelPositionsRAS else None

    def segmentLabelPositionsOnSlicesRAS(self, segmentationNode, segmentID, sliceNodes):
        """Return label positions for all slice views overlapped by segment bounds."""
        labelPositionsRAS = []
        for sliceNode in sliceNodes:
            labelPositionRAS = self.segmentLabelPositionOnSliceRAS(
                segmentationNode,
                segmentID,
                sliceNode,
            )
            if labelPositionRAS is not None:
                labelPositionsRAS.append(
                    {
                        "positionRAS": labelPositionRAS,
                        "viewNodeID": sliceNode.GetID() if sliceNode else "",
                    }
                )
        return labelPositionsRAS

    def segmentLabelPositionOnSliceRAS(self, segmentationNode, segmentID, sliceNode):
        """Return a label point when segment bounds overlap one visible slice view."""
        boundsRAS = self.segmentBoundsRAS(segmentationNode, segmentID)
        if boundsRAS is None:
            return None
        plane = self.slicePlaneRAS(sliceNode)
        if plane is None:
            return None

        boundsCornersRAS = self.boundsCornersRAS(boundsRAS)
        projectedBounds = self.projectBoundsToSlice(boundsCornersRAS, plane)
        if not self.projectedBoundsIntersectSliceView(projectedBounds, plane):
            return None

        centerRAS = self.segmentCenterRAS(segmentationNode, segmentID)
        if centerRAS is None:
            centerRAS = self.boundsCenterRAS(boundsRAS)
        centerSlice = self.projectPointToSlice(centerRAS, plane)
        margin = self.segmentLabelMarginRAS(boundsRAS)
        labelX = self.labelXPositionInsideSliceView(projectedBounds, plane, margin)
        labelY = self.clamp(
            centerSlice["y"],
            -plane["halfHeight"] + margin,
            plane["halfHeight"] - margin,
        )
        return self.add(
            plane["origin"],
            self.add(
                self.scale(plane["xAxis"], labelX),
                self.scale(plane["yAxis"], labelY),
            ),
        )

    def slicePlaneRAS(self, sliceNode):
        """Return slice plane origin, axes, and field-of-view in RAS coordinates."""
        if not sliceNode:
            return None
        try:
            sliceToRAS = sliceNode.GetSliceToRAS()
        except Exception:
            return None
        if not sliceToRAS:
            return None
        origin = [float(sliceToRAS.GetElement(row, 3)) for row in range(3)]
        xAxis = self.normalized(
            [float(sliceToRAS.GetElement(row, 0)) for row in range(3)]
        )
        yAxis = self.normalized(
            [float(sliceToRAS.GetElement(row, 1)) for row in range(3)]
        )
        normal = self.normalized(
            [float(sliceToRAS.GetElement(row, 2)) for row in range(3)]
        )
        if not xAxis or not yAxis or not normal:
            return None
        try:
            fieldOfView = sliceNode.GetFieldOfView()
            halfWidth = max(float(fieldOfView[0]) * 0.5, 0.0)
            halfHeight = max(float(fieldOfView[1]) * 0.5, 0.0)
        except Exception:
            halfWidth = float("inf")
            halfHeight = float("inf")
        return {
            "origin": origin,
            "xAxis": xAxis,
            "yAxis": yAxis,
            "normal": normal,
            "halfWidth": halfWidth,
            "halfHeight": halfHeight,
        }

    def projectBoundsToSlice(self, boundsCornersRAS, plane):
        """Project RAS bounds corners into one slice coordinate system."""
        projectedPoints = [
            self.projectPointToSlice(cornerRAS, plane)
            for cornerRAS in boundsCornersRAS
        ]
        return {
            "minX": min(point["x"] for point in projectedPoints),
            "maxX": max(point["x"] for point in projectedPoints),
            "minY": min(point["y"] for point in projectedPoints),
            "maxY": max(point["y"] for point in projectedPoints),
            "minDistance": min(point["distance"] for point in projectedPoints),
            "maxDistance": max(point["distance"] for point in projectedPoints),
        }

    def projectPointToSlice(self, pointRAS, plane):
        """Project one RAS point into slice x/y/distance coordinates."""
        pointFromOrigin = self.subtract(pointRAS, plane["origin"])
        return {
            "x": self.dot(pointFromOrigin, plane["xAxis"]),
            "y": self.dot(pointFromOrigin, plane["yAxis"]),
            "distance": self.dot(pointFromOrigin, plane["normal"]),
        }

    def projectedBoundsIntersectSliceView(self, projectedBounds, plane):
        """Return True when projected segment bounds overlap the visible slice area."""
        if (
            projectedBounds["minDistance"] > SEGMENT_NAME_LABEL_SLICE_TOLERANCE_MM
            or projectedBounds["maxDistance"] < -SEGMENT_NAME_LABEL_SLICE_TOLERANCE_MM
        ):
            return False
        if projectedBounds["maxX"] < -plane["halfWidth"]:
            return False
        if projectedBounds["minX"] > plane["halfWidth"]:
            return False
        if projectedBounds["maxY"] < -plane["halfHeight"]:
            return False
        if projectedBounds["minY"] > plane["halfHeight"]:
            return False
        return True

    def labelXPositionInsideSliceView(self, projectedBounds, plane, margin):
        """Place the label beside a segment while keeping it inside the slice view."""
        rightSideX = projectedBounds["maxX"] + margin
        leftSideX = projectedBounds["minX"] - margin
        if rightSideX <= plane["halfWidth"] - margin:
            return rightSideX
        if leftSideX >= -plane["halfWidth"] + margin:
            return leftSideX
        return self.clamp(
            projectedBounds["maxX"],
            -plane["halfWidth"] + margin,
            plane["halfWidth"] - margin,
        )

    def segmentLabelPositionRAS(self, segmentationNode, segmentID):
        """Return an RAS label point near, but outside, the segment bounds."""
        centerRAS = self.segmentCenterRAS(segmentationNode, segmentID)
        if centerRAS is None:
            return None

        boundsRAS = self.segmentBoundsRAS(segmentationNode, segmentID)
        if boundsRAS is None:
            return [
                centerRAS[0] + SEGMENT_NAME_LABEL_OFFSET_MINIMUM_MM,
                centerRAS[1] + SEGMENT_NAME_LABEL_OFFSET_MINIMUM_MM,
                centerRAS[2],
            ]

        dimensions = [
            max(boundsRAS[1] - boundsRAS[0], 0.0),
            max(boundsRAS[3] - boundsRAS[2], 0.0),
            max(boundsRAS[5] - boundsRAS[4], 0.0),
        ]
        margin = max(
            max(dimensions) * SEGMENT_NAME_LABEL_OFFSET_FRACTION,
            SEGMENT_NAME_LABEL_OFFSET_MINIMUM_MM,
        )
        return [
            boundsRAS[1] + margin,
            centerRAS[1],
            centerRAS[2],
        ]

    def segmentBoundsRAS(self, segmentationNode, segmentID):
        """Return segment bounds in RAS coordinates when Slicer can calculate them."""
        if not self.isValidSegmentID(segmentationNode, segmentID):
            return None
        boundsRAS = [0.0] * 6
        try:
            segmentationNode.GetSegmentBounds(segmentID, boundsRAS)
        except Exception:
            logging.debug("Could not get segment bounds for label placement", exc_info=True)
            return None
        if boundsRAS[0] > boundsRAS[1] or boundsRAS[2] > boundsRAS[3]:
            return None
        if boundsRAS[4] > boundsRAS[5]:
            return None
        return [float(value) for value in boundsRAS]

    def boundsCornersRAS(self, boundsRAS):
        """Return the eight RAS corners for a bounds tuple."""
        return [
            [x, y, z]
            for x in (boundsRAS[0], boundsRAS[1])
            for y in (boundsRAS[2], boundsRAS[3])
            for z in (boundsRAS[4], boundsRAS[5])
        ]

    def boundsCenterRAS(self, boundsRAS):
        """Return the center point of an RAS bounds tuple."""
        return [
            (boundsRAS[0] + boundsRAS[1]) * 0.5,
            (boundsRAS[2] + boundsRAS[3]) * 0.5,
            (boundsRAS[4] + boundsRAS[5]) * 0.5,
        ]

    def segmentLabelMarginRAS(self, boundsRAS):
        """Return a margin for putting labels outside segment bounds."""
        dimensions = [
            max(boundsRAS[1] - boundsRAS[0], 0.0),
            max(boundsRAS[3] - boundsRAS[2], 0.0),
            max(boundsRAS[5] - boundsRAS[4], 0.0),
        ]
        return max(
            max(dimensions) * SEGMENT_NAME_LABEL_OFFSET_FRACTION,
            SEGMENT_NAME_LABEL_OFFSET_MINIMUM_MM,
        )

    def add(self, firstVector, secondVector):
        """Add two 3D vectors."""
        return [firstVector[index] + secondVector[index] for index in range(3)]

    def subtract(self, firstVector, secondVector):
        """Subtract two 3D vectors."""
        return [firstVector[index] - secondVector[index] for index in range(3)]

    def scale(self, vector, scaleFactor):
        """Scale a 3D vector."""
        return [vector[index] * scaleFactor for index in range(3)]

    def dot(self, firstVector, secondVector):
        """Return the dot product of two 3D vectors."""
        return sum(firstVector[index] * secondVector[index] for index in range(3))

    def normalized(self, vector):
        """Return a normalized 3D vector, or None for a zero-length vector."""
        length = sum(component * component for component in vector) ** 0.5
        if length == 0:
            return None
        return [component / length for component in vector]

    def clamp(self, value, minimumValue, maximumValue):
        """Clamp a numeric value between a minimum and maximum."""
        if minimumValue > maximumValue:
            return value
        return max(minimumValue, min(value, maximumValue))

    def applySliceZoom(self, zoomFactor, baseFieldOfViewBySliceNodeID):
        """Apply the zoom factor by reducing each slice view field-of-view."""
        zoomFactor = max(1.0, float(zoomFactor))
        for sliceNode in slicer.util.getNodesByClass("vtkMRMLSliceNode"):
            sliceNodeID = sliceNode.GetID()
            if sliceNodeID not in baseFieldOfViewBySliceNodeID:
                baseFieldOfViewBySliceNodeID[sliceNodeID] = list(sliceNode.GetFieldOfView())

            baseFieldOfView = baseFieldOfViewBySliceNodeID[sliceNodeID]
            newFieldOfView = [
                max(baseFieldOfView[0] / zoomFactor, 1.0),
                max(baseFieldOfView[1] / zoomFactor, 1.0),
                baseFieldOfView[2],
            ]
            sliceNode.SetFieldOfView(newFieldOfView[0], newFieldOfView[1], newFieldOfView[2])
            sliceNode.UpdateMatrices()

    def ensureSliceFieldOfViewBaseline(self, baseFieldOfViewBySliceNodeID):
        """Create a fitted slice field-of-view baseline only when it is missing."""
        sliceNodes = slicer.util.getNodesByClass("vtkMRMLSliceNode")
        if (
            baseFieldOfViewBySliceNodeID
            and all(sliceNode.GetID() in baseFieldOfViewBySliceNodeID for sliceNode in sliceNodes)
        ):
            return
        self.resetSliceFieldOfViewBaseline(baseFieldOfViewBySliceNodeID)

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
                        logging.debug(
                            "Could not reset slice field of view before applying jump zoom",
                            exc_info=True,
                        )

        for sliceNode in slicer.util.getNodesByClass("vtkMRMLSliceNode"):
            baseFieldOfViewBySliceNodeID[sliceNode.GetID()] = list(
                sliceNode.GetFieldOfView()
            )

    def ensureSourceVolumeVisible(self, sourceVolumeNode):
        """Set the selected source volume as the slice-view background."""
        if not sourceVolumeNode:
            return
        try:
            slicer.util.setSliceViewerLayers(background=sourceVolumeNode, fit=False)
            return
        except Exception:
            logging.debug(
                "setSliceViewerLayers failed; falling back to slice composite nodes",
                exc_info=True,
            )

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
        wasModifying = None
        if hasattr(displayNode, "StartModify"):
            wasModifying = displayNode.StartModify()
        try:
            displayNode.SetVisibility(bool(visible))
            if hasattr(displayNode, "SetVisibility2D"):
                displayNode.SetVisibility2D(bool(visible))
            if hasattr(displayNode, "SetVisibility3D"):
                displayNode.SetVisibility3D(bool(visible))
        finally:
            if wasModifying is not None and hasattr(displayNode, "EndModify"):
                displayNode.EndModify(wasModifying)
            else:
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

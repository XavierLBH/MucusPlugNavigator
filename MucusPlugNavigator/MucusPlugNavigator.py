import logging

import qt
import slicer
import vtk
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin


#
# MucusPlugNavigator
#


class MucusPlugNavigator(ScriptedLoadableModule):
    """Navigate, inspect, edit, delete, and count existing mucus plug segments."""

    def __init__(self, parent):
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
    def __init__(self, parent=None):
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

    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)

        self.segmentEditorNode = self._getOrCreateSegmentEditorNode()

        selectorsFrame = qt.QFrame()
        selectorsLayout = qt.QFormLayout()
        selectorsFrame.setLayout(selectorsLayout)
        selectorsLayout.setContentsMargins(0, 0, 0, 0)

        self.segmentationSelector = slicer.qMRMLNodeComboBox()
        self.segmentationSelector.nodeTypes = ["vtkMRMLSegmentationNode"]
        self.segmentationSelector.selectNodeUponCreation = True
        self.segmentationSelector.addEnabled = False
        self.segmentationSelector.removeEnabled = False
        self.segmentationSelector.noneEnabled = True
        self.segmentationSelector.showHidden = False
        self.segmentationSelector.showChildNodeTypes = False
        self.segmentationSelector.setMRMLScene(slicer.mrmlScene)
        self.segmentationSelector.setToolTip("Select the mucus segmentation. Each segment is treated as one mucus plug.")
        selectorsLayout.addRow("Segmentation:", self.segmentationSelector)

        self.sourceVolumeSelector = slicer.qMRMLNodeComboBox()
        self.sourceVolumeSelector.nodeTypes = ["vtkMRMLScalarVolumeNode"]
        self.sourceVolumeSelector.selectNodeUponCreation = True
        self.sourceVolumeSelector.addEnabled = False
        self.sourceVolumeSelector.removeEnabled = False
        self.sourceVolumeSelector.noneEnabled = True
        self.sourceVolumeSelector.showHidden = False
        self.sourceVolumeSelector.showChildNodeTypes = False
        self.sourceVolumeSelector.setMRMLScene(slicer.mrmlScene)
        self.sourceVolumeSelector.setToolTip("Select the CT source volume used by Segment Editor.")
        selectorsLayout.addRow("Source volume:", self.sourceVolumeSelector)

        self.layout.addWidget(selectorsFrame)

        controlsFrame = qt.QFrame()
        controlsLayout = qt.QGridLayout()
        controlsFrame.setLayout(controlsLayout)
        controlsLayout.setContentsMargins(0, 4, 0, 4)
        controlsLayout.setHorizontalSpacing(6)
        controlsLayout.setVerticalSpacing(4)

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
        self.zoomSpinBox.setRange(1.0, 10.0)
        self.zoomSpinBox.setDecimals(1)
        self.zoomSpinBox.setSingleStep(0.5)
        self.zoomSpinBox.setSuffix("x")
        self.zoomSpinBox.setValue(1.0)
        self.zoomSpinBox.setToolTip("Higher zoom uses a smaller slice field of view after jumping.")
        controlsLayout.addWidget(self.zoomSpinBox, 1, 1)

        self.jumpButton = qt.QPushButton("Jump")
        self.jumpButton.setToolTip("Jump slice views to the currently selected mucus plug.")
        controlsLayout.addWidget(self.jumpButton, 1, 2)

        self.lastButton = qt.QPushButton("Last")
        self.lastButton.setToolTip("Select the previous segment in segmentation order and jump to it.")
        controlsLayout.addWidget(self.lastButton, 1, 3)

        self.nextButton = qt.QPushButton("Next")
        self.nextButton.setToolTip("Select the next segment in segmentation order and jump to it.")
        controlsLayout.addWidget(self.nextButton, 1, 4)

        self._setNavigationButtonIcons()
        self.layout.addWidget(controlsFrame)

        segmentToolbarFrame = qt.QFrame()
        segmentToolbarLayout = qt.QGridLayout()
        segmentToolbarFrame.setLayout(segmentToolbarLayout)
        segmentToolbarLayout.setContentsMargins(0, 4, 0, 4)
        segmentToolbarLayout.setHorizontalSpacing(6)
        segmentToolbarLayout.setVerticalSpacing(4)

        self.addButton = qt.QPushButton("Add")
        self.addButton.setToolTip("Add a new segment to the selected segmentation.")
        segmentToolbarLayout.addWidget(self.addButton, 0, 0)

        self.show3DButton = qt.QPushButton("Show 3D")
        self.show3DButton.setToolTip("Toggle 3D display for the selected segmentation.")
        segmentToolbarLayout.addWidget(self.show3DButton, 0, 1)

        self.deleteButton = qt.QPushButton("Delete")
        self.deleteButton.setToolTip("Delete only the currently selected mucus plug segment.")
        segmentToolbarLayout.addWidget(self.deleteButton, 0, 2)

        self.measureButton = qt.QPushButton("Measure")
        self.measureButton.setToolTip("Calculate volume and length for the currently selected mucus plug.")
        segmentToolbarLayout.addWidget(self.measureButton, 0, 3)

        self.noEditingButton = qt.QPushButton("No editing")
        self.noEditingButton.setToolTip("Turn off the active Segment Editor effect.")
        segmentToolbarLayout.addWidget(self.noEditingButton, 1, 0)

        self.paintButton = qt.QPushButton("Paint")
        self.paintButton.setToolTip("Activate the Segment Editor Paint effect.")
        segmentToolbarLayout.addWidget(self.paintButton, 1, 1)

        self.eraseButton = qt.QPushButton("Erase")
        self.eraseButton.setToolTip("Activate the Segment Editor Erase effect.")
        segmentToolbarLayout.addWidget(self.eraseButton, 1, 2)

        self.layout.addWidget(segmentToolbarFrame)

        self.segmentEditorWidget = slicer.qMRMLSegmentEditorWidget()
        self.segmentEditorWidget.setMRMLScene(slicer.mrmlScene)
        self.segmentEditorWidget.setMRMLSegmentEditorNode(self.segmentEditorNode)
        self._configureEmbeddedSegmentEditor()
        self.layout.addWidget(self.segmentEditorWidget)
        self._prepareCustomSegmentToolbar()

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
        self.zoomSpinBox.connect("valueChanged(double)", self.onZoomChanged)
        self.segmentEditorWidget.connect("currentSegmentIDChanged(QString)", self.onCurrentSegmentChanged)

        self.onSegmentationNodeChanged(self.segmentationSelector.currentNode())
        self.onSourceVolumeNodeChanged(self.sourceVolumeSelector.currentNode())
        self.updateSegmentCountAndButtons()

    def cleanup(self):
        self._removeSegmentationObservers()
        if self.segmentEditorWidget:
            self.segmentEditorWidget.setActiveEffect(None)
            self.segmentEditorWidget.removeViewObservations()
            self.segmentEditorWidget.uninstallKeyboardShortcuts()
            self.segmentEditorWidget = None

    def enter(self):
        if self.segmentEditorWidget:
            self.segmentEditorWidget.setMRMLScene(slicer.mrmlScene)
            self.segmentEditorWidget.setupViewObservations()
            self.segmentEditorWidget.installKeyboardShortcuts()
            self.updateSegmentCountAndButtons()

    def exit(self):
        if self.segmentEditorWidget:
            self.segmentEditorWidget.setActiveEffect(None)
            self.segmentEditorWidget.removeViewObservations()
            self.segmentEditorWidget.uninstallKeyboardShortcuts()

    def _getOrCreateSegmentEditorNode(self):
        singletonTag = "MucusPlugNavigatorSegmentEditor"
        node = slicer.mrmlScene.GetSingletonNode(singletonTag, "vtkMRMLSegmentEditorNode")
        if node:
            return node

        node = slicer.mrmlScene.CreateNodeByClass("vtkMRMLSegmentEditorNode")
        node.UnRegister(None)
        node.SetSingletonTag(singletonTag)
        node.SetName("MucusPlugNavigatorSegmentEditor")
        slicer.mrmlScene.AddNode(node)
        return node

    def _configureEmbeddedSegmentEditor(self):
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
        addButton = self._findSegmentEditorButton("Add")
        show3DButton = self._findSegmentEditorButton("Show 3D")
        removeButton = self._findSegmentEditorButton("Remove")
        noneButton = self._findSegmentEditorButton("None")
        paintButton = self._findSegmentEditorButton("Paint")
        eraseButton = self._findSegmentEditorButton("Erase")

        self._segmentEditorAddButton = addButton
        self._segmentEditorShow3DButton = show3DButton

        self._copyButtonStyle(addButton, self.addButton)
        self._copyButtonStyle(show3DButton, self.show3DButton)
        self._copyButtonStyle(removeButton if removeButton else addButton, self.deleteButton)
        self._copyButtonStyle(addButton, self.measureButton)
        self._copyButtonStyle(noneButton if noneButton else addButton, self.noEditingButton)
        self._copyButtonStyle(paintButton if paintButton else addButton, self.paintButton)
        self._copyButtonStyle(eraseButton if eraseButton else addButton, self.eraseButton)
        self._copyButtonStyle(addButton, self.jumpButton)
        self._copyButtonStyle(addButton, self.lastButton)
        self._copyButtonStyle(addButton, self.nextButton)

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
        if not sourceButton:
            return
        try:
            icon = sourceButton.icon
            if callable(icon):
                icon = icon()
            targetButton.setIcon(icon)
        except Exception:
            logging.debug("Could not copy Segment Editor effect icon", exc_info=True)

    def _hideSegmentEditorToolbarButtons(self):
        for buttonText in ("Add", "Remove", "Show 3D"):
            button = self._findSegmentEditorButton(buttonText)
            if button:
                button.hide()
        if hasattr(self.segmentEditorWidget, "setAddRemoveSegmentButtonsVisible"):
            self.segmentEditorWidget.setAddRemoveSegmentButtonsVisible(False)
        if hasattr(self.segmentEditorWidget, "setShow3DButtonVisible"):
            self.segmentEditorWidget.setShow3DButtonVisible(False)

    def _hideSegmentEditorEffectButtons(self):
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
        effectButtons = []
        for button in self.segmentEditorWidget.findChildren(qt.QAbstractButton):
            if self._buttonText(button) in effectButtonTexts:
                effectButtons.append(button)

        for button in effectButtons:
            button.hide()

        parentCounts = {}
        for button in effectButtons:
            parent = button.parent()
            if parent:
                parentCounts[parent] = parentCounts.get(parent, 0) + 1
        if parentCounts:
            mostLikelyEffectGrid = max(parentCounts, key=parentCounts.get)
            effectGridContainsToolbarButton = any(
                self._buttonText(button) in ("Add", "Remove", "Show 3D")
                for button in mostLikelyEffectGrid.findChildren(qt.QAbstractButton)
            )
            if parentCounts[mostLikelyEffectGrid] >= 2 and mostLikelyEffectGrid != self.segmentEditorWidget and not effectGridContainsToolbarButton:
                mostLikelyEffectGrid.hide()

    def _copyButtonStyle(self, sourceButton, targetButton):
        if not sourceButton:
            return
        try:
            targetButton.setIconSize(sourceButton.iconSize)
        except Exception:
            logging.debug("Could not copy Segment Editor toolbar button style", exc_info=True)

    def _normalizeButtonSizePolicies(self):
        for button in (
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
        ):
            button.setMinimumSize(qt.QSize(92, 32))
            button.setMaximumSize(qt.QSize(16777215, 16777215))
            button.setSizePolicy(qt.QSizePolicy.Preferred, qt.QSizePolicy.Fixed)

    def _findSegmentEditorButton(self, buttonText):
        for button in self.segmentEditorWidget.findChildren(qt.QAbstractButton):
            if self._buttonText(button) == buttonText:
                return button
        return None

    def _buttonText(self, button):
        text = ""
        if hasattr(button, "text"):
            text = button.text
            if callable(text):
                text = text()
        return str(text).replace("&", "").strip()

    def _setNavigationButtonIcons(self):
        try:
            style = slicer.util.mainWindow().style()
            self.lastButton.setIcon(style.standardIcon(qt.QStyle.SP_ArrowLeft))
            self.nextButton.setIcon(style.standardIcon(qt.QStyle.SP_ArrowRight))
        except Exception:
            logging.debug("Could not set standard button icons", exc_info=True)

    def onSegmentationNodeChanged(self, segmentationNode):
        self.segmentEditorWidget.setSegmentationNode(segmentationNode)
        self.logic.ensureSegmentationVisible(segmentationNode)
        self._observeSegmentation(segmentationNode)
        self._selectFirstSegmentIfNeeded()
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()

    def onSourceVolumeNodeChanged(self, sourceVolumeNode):
        if hasattr(self.segmentEditorWidget, "setSourceVolumeNode"):
            self.segmentEditorWidget.setSourceVolumeNode(sourceVolumeNode)
        else:
            self.segmentEditorWidget.setMasterVolumeNode(sourceVolumeNode)
        self.logic.ensureSourceVolumeVisible(sourceVolumeNode)
        self._sliceBaseFieldOfViewByID = {}
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()

    def onCurrentSegmentChanged(self, segmentID):
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()

    def onObservedSegmentationChanged(self, caller=None, event=None):
        self._selectFirstSegmentIfNeeded()
        self.resetCurrentSegmentMeasurements()
        self.updateSegmentCountAndButtons()

    def onZoomChanged(self, value):
        segmentID = self.currentSegmentID()
        if self.logic.isValidSegmentID(self.segmentationNode(), segmentID):
            self.jumpToSegment(segmentID)

    def onJumpButton(self, checked=False):
        segmentID = self.currentSegmentID()
        if not self.logic.isValidSegmentID(self.segmentationNode(), segmentID):
            self._selectFirstSegmentIfNeeded()
            segmentID = self.currentSegmentID()
        self.jumpToSegment(segmentID)

    def onLastButton(self, checked=False):
        segmentationNode = self.segmentationNode()
        lastSegmentID = self.logic.previousSegmentID(segmentationNode, self.currentSegmentID(), wrap=True)
        if not lastSegmentID:
            return
        self.segmentEditorWidget.setCurrentSegmentID(lastSegmentID)
        self.jumpToSegment(lastSegmentID)
        self.updateSegmentCountAndButtons()

    def onNextButton(self, checked=False):
        segmentationNode = self.segmentationNode()
        nextSegmentID = self.logic.nextSegmentID(segmentationNode, self.currentSegmentID(), wrap=True)
        if not nextSegmentID:
            return
        self.segmentEditorWidget.setCurrentSegmentID(nextSegmentID)
        self.jumpToSegment(nextSegmentID)
        self.updateSegmentCountAndButtons()

    def onAddButton(self, checked=False):
        if self._segmentEditorAddButton:
            self._segmentEditorAddButton.click()
        else:
            segmentationNode = self.segmentationNode()
            if segmentationNode:
                segmentID = segmentationNode.GetSegmentation().AddEmptySegment()
                self.segmentEditorWidget.setCurrentSegmentID(segmentID)
        self.updateSegmentCountAndButtons()

    def onShow3DButton(self, checked=False):
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

    def onNoEditingButton(self, checked=False):
        self.segmentEditorWidget.setActiveEffect(None)

    def onMeasureButton(self, checked=False):
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

    def onPaintButton(self, checked=False):
        self.segmentEditorWidget.setActiveEffectByName("Paint")

    def onEraseButton(self, checked=False):
        self.segmentEditorWidget.setActiveEffectByName("Erase")

    def onDeleteButton(self, checked=False):
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

    def jumpToSegment(self, segmentID):
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
        return self.segmentationSelector.currentNode()

    def sourceVolumeNode(self):
        return self.sourceVolumeSelector.currentNode()

    def currentSegmentID(self):
        if not self.segmentEditorWidget:
            return ""
        return self.segmentEditorWidget.currentSegmentID()

    def updateSegmentCountAndButtons(self):
        segmentationNode = self.segmentationNode()
        count = self.logic.segmentCount(segmentationNode)
        self.countLabel.setText("Mucus plug count: {}".format(count))

        hasSegments = count > 0
        hasCurrentSegment = self.logic.isValidSegmentID(segmentationNode, self.currentSegmentID())
        hasSegmentation = segmentationNode is not None
        self.addButton.enabled = hasSegmentation
        self.show3DButton.enabled = hasSegmentation
        self.jumpButton.enabled = hasSegments
        self.lastButton.enabled = hasSegments
        self.nextButton.enabled = hasSegments
        self.deleteButton.enabled = hasCurrentSegment
        self.measureButton.enabled = hasCurrentSegment
        self.noEditingButton.enabled = hasSegmentation
        self.paintButton.enabled = hasCurrentSegment
        self.eraseButton.enabled = hasCurrentSegment

    def resetCurrentSegmentMeasurements(self):
        self.volumeLabel.setText("Volume: not calculated")
        self.lengthLabel.setText("Length: not calculated")

    def _selectFirstSegmentIfNeeded(self):
        segmentationNode = self.segmentationNode()
        if not segmentationNode:
            return
        currentSegmentID = self.currentSegmentID()
        if self.logic.isValidSegmentID(segmentationNode, currentSegmentID):
            return
        segmentIDs = self.logic.segmentIDs(segmentationNode)
        self.segmentEditorWidget.setCurrentSegmentID(segmentIDs[0] if segmentIDs else "")

    def _observeSegmentation(self, segmentationNode):
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
        if self._observedSegmentation:
            for tag in self._segmentationObserverTags:
                self._observedSegmentation.RemoveObserver(tag)
        self._observedSegmentation = None
        self._segmentationObserverTags = []


#
# MucusPlugNavigatorLogic
#


class MucusPlugNavigatorLogic(ScriptedLoadableModuleLogic):
    def segmentIDs(self, segmentationNode):
        if not segmentationNode:
            return []
        segmentation = segmentationNode.GetSegmentation()
        if not segmentation:
            return []
        return [segmentation.GetNthSegmentID(index) for index in range(segmentation.GetNumberOfSegments())]

    def segmentCount(self, segmentationNode):
        return len(self.segmentIDs(segmentationNode))

    def segmentVoxelMetrics(self, segmentationNode, segmentID, referenceVolumeNode=None):
        if not self.isValidSegmentID(segmentationNode, segmentID):
            return None

        try:
            import numpy as np

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
            occupiedVoxelCoordinates = np.argwhere(segmentArray != 0)
        except Exception:
            logging.exception("Could not compute segment voxel measurements for segment: %s", segmentID)
            return None

        volumePixels = int(occupiedVoxelCoordinates.shape[0])
        if volumePixels == 0:
            return {"volumePixels": 0, "lengthPixels": 0}
        if volumePixels == 1:
            return {"volumePixels": volumePixels, "lengthPixels": 1}

        centeredCoordinates = occupiedVoxelCoordinates - occupiedVoxelCoordinates.mean(axis=0)
        try:
            _, _, principalAxes = np.linalg.svd(centeredCoordinates, full_matrices=False)
            principalAxis = principalAxes[0]
            projectedCoordinates = occupiedVoxelCoordinates.dot(principalAxis)
            lengthPixels = int(round(projectedCoordinates.max() - projectedCoordinates.min() + 1))
        except Exception:
            voxelDimensions = occupiedVoxelCoordinates.max(axis=0) - occupiedVoxelCoordinates.min(axis=0) + 1
            lengthPixels = int(voxelDimensions.max())

        return {"volumePixels": volumePixels, "lengthPixels": max(lengthPixels, 1)}

    def isValidSegmentID(self, segmentationNode, segmentID):
        if not segmentationNode or not segmentID:
            return False
        return segmentID in self.segmentIDs(segmentationNode)

    def nextSegmentID(self, segmentationNode, currentSegmentID, wrap=True):
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
        centerRAS = self.segmentCenterRAS(segmentationNode, segmentID)
        if centerRAS is None:
            logging.warning("Could not find center for segment: %s", segmentID)
            return

        self.resetSliceFieldOfViewBaseline(baseFieldOfViewBySliceNodeID)
        for sliceNode in slicer.util.getNodesByClass("vtkMRMLSliceNode"):
            sliceNode.JumpSliceByCentering(centerRAS[0], centerRAS[1], centerRAS[2])

        self.applySliceZoom(zoomFactor, baseFieldOfViewBySliceNodeID)

    def segmentCenterRAS(self, segmentationNode, segmentID):
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
    def setUp(self):
        slicer.mrmlScene.Clear()

    def runTest(self):
        self.setUp()
        self.test_MucusPlugNavigatorLogic()

    def test_MucusPlugNavigatorLogic(self):
        logic = MucusPlugNavigatorLogic()
        self.assertEqual(logic.segmentCount(None), 0)
        self.assertEqual(logic.nextSegmentID(None, ""), "")
        self.assertEqual(logic.previousSegmentID(None, ""), "")

import pathlib
import objc
import AppKit
from vanilla import *
from fontTools.misc.arrayTools import offsetRect, scaleRect, unionRect
from fontgoggles.font import defaultSortSpec, sniffFontType, sortedFontPathsAndNumbers
from fontgoggles.mac.drawing import *
from fontgoggles.mac.misc import textAlignments
from fontgoggles.misc.decorators import suppressAndLogException
from fontgoggles.misc.properties import delegateProperty, hookedProperty
from fontgoggles.misc.rectTree import RectTree


fontItemMinimumSize = 60
fontItemMaximumSize = 1500


class FGFontListView(AppKit.NSView):

    def init(self):
        self = super().init()
        self.registerForDraggedTypes_([AppKit.NSFilenamesPboardType])
        return self

    def acceptsFirstResponder(self):
        return True

    def becomeFirstResponder(self):
        return True

    def mouseDown_(self, event):
        self.vanillaWrapper().mouseDown(event)

    def keyDown_(self, event):
        self.vanillaWrapper().keyDown(event)

    def subscribeToMagnification_(self, scrollView):
        AppKit.NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "_liveMagnifyWillStart:", AppKit.NSScrollViewWillStartLiveMagnifyNotification,
            scrollView)
        AppKit.NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "_liveMagnifyDidEnd:", AppKit.NSScrollViewDidEndLiveMagnifyNotification,
            scrollView)

    _nestedZoom = 0

    @suppressAndLogException
    def _liveMagnifyWillStart_(self, notification):
        if self._nestedZoom == 0:
            self._savedClipBounds = self.superview().bounds()
            scrollView = notification.object()
            fontList = self.vanillaWrapper()
            minMag = (fontItemMinimumSize / fontList.itemSize)
            maxMag = (fontItemMaximumSize / fontList.itemSize)
            scrollView.setMinMagnification_(minMag)
            scrollView.setMaxMagnification_(maxMag)
        self._nestedZoom += 1

    @suppressAndLogException
    def _liveMagnifyDidEnd_(self, notification):
        self._nestedZoom -= 1
        if self._nestedZoom == 0:
            scrollView = notification.object()
            clipView = self.superview()

            finalBounds = clipView.bounds()
            x, y = finalBounds.origin
            dy = clipView.frame().size.height - clipView.bounds().size.height
            scrollX, scrollY = x, y - dy
            magnification = scrollView.magnification()
            scrollView.setMagnification_(1.0)

            fontList = self.vanillaWrapper()
            newItemSize = round(max(fontItemMinimumSize,
                                    min(fontItemMaximumSize, fontList.itemSize * magnification)))
            actualMag = newItemSize / fontList.itemSize
            fontList.resizeFontItems(newItemSize)
            newBounds = ((round(actualMag * scrollX), round(actualMag * scrollY)), self._savedClipBounds.size)
            scrollView.setMagnification_(1.0)
            newBounds = clipView.constrainBoundsRect_(newBounds)
            clipView.setBounds_(newBounds)
            scrollView.setMagnification_(1.0)
            self._savedClipBounds = None

    _dragPosView = None

    @suppressAndLogException
    def draggingEntered_(self, draggingInfo):
        if any(sniffFontType(path) or path.is_dir() for path in self._iterateFilesFromDraggingInfo(draggingInfo)):
            self._weHaveValidDrag = True
            if self._dragPosView is None:
                self._dragPosView = AppKit.NSView.alloc().init()
                self._dragPosView.setBackgroundColor_(AppKit.NSColor.textColor())
            index, frame = self._getDropInsertionIndexAndRect_(draggingInfo)
            self._dragPosView.setFrame_(frame)
            self.addSubview_(self._dragPosView)
            return AppKit.NSDragOperationEvery
        else:
            self._weHaveValidDrag = False
            return AppKit.NSDragOperationNone

    @suppressAndLogException
    def draggingUpdated_(self, draggingInfo):
        # print("updated", draggingInfo)
        if self._weHaveValidDrag:
            index, frame = self._getDropInsertionIndexAndRect_(draggingInfo)
            self._dragPosView.animator().setFrame_(frame)
            return AppKit.NSDragOperationEvery
        else:
            return AppKit.NSDragOperationNone

    def draggingExited_(self, draggingInfo):
        self._dragPosView.removeFromSuperview()
        self._dragPosView = None

    @objc.signature(b"Z@:@")  # PyObjC bug?
    @suppressAndLogException
    def draggingEnded_(self, draggingInfo):
        if self._dragPosView is not None:
            self._dragPosView.removeFromSuperview()
            self._dragPosView = None

    def _getDropInsertionIndexAndRect_(self, draggingInfo):
        point = self.convertPoint_fromView_(draggingInfo.draggingLocation(), None)
        fontList = self.vanillaWrapper()
        itemSize = fontList.itemSize
        vertical = fontList.vertical
        frame = self._dragPosView.frame()
        frame.origin[1 - vertical] = max(0, itemSize * round(point[1 - vertical] / itemSize))
        frame.size[vertical] = self.frame().size[vertical]
        dropBarSize = 2
        frame.size[1 - vertical] = dropBarSize
        index = round(frame.origin[1 - vertical]) // itemSize
        if frame.origin[1 - vertical] >= self.frame().size[1 - vertical]:
            frame.origin[1 - vertical] = self.frame().size[1 - vertical] - dropBarSize
        index = fontList.getNumFontItems() - index
        return index, frame

    def prepareForDragOperation_(self, draggingInfo):
        return True

    def performDragOperation_(self, draggingInfo):
        index, frame = self._getDropInsertionIndexAndRect_(draggingInfo)
        self.vanillaWrapper().insertFonts(self._iterateFilesFromDraggingInfo(draggingInfo), index)
        return True

    @staticmethod
    def _iterateFilesFromDraggingInfo(draggingInfo):
        for path in draggingInfo.draggingPasteboard().propertyListForType_(AppKit.NSFilenamesPboardType):
            yield pathlib.Path(path)


arrowKeyDefs = {
    AppKit.NSUpArrowFunctionKey: (-1, 1),
    AppKit.NSDownArrowFunctionKey: (1, 1),
    AppKit.NSLeftArrowFunctionKey: (-1, 0),
    AppKit.NSRightArrowFunctionKey: (1, 0),
}


class FontList(Group):

    nsViewClass = FGFontListView

    def __init__(self, project, width, itemSize, selectionChangedCallback=None,
                 glyphSelectionChangedCallback=None, arrowKeyCallback=None):
        super().__init__((0, 0, width, 900))
        self.project = None  # Dummy, so we can set up other attrs first
        self._selection = set()  # a set of indices
        self.vertical = 0  # 0, 1: it is also an index into (x, y) tuples
        self.itemSize = itemSize
        self.align = "left"
        self._selectionChangedCallback = selectionChangedCallback
        self._glyphSelectionChangedCallback = glyphSelectionChangedCallback
        self._arrowKeyCallback = arrowKeyCallback
        self._lastItemClicked = None
        self.project = project
        self.setupFontItems()

    def _glyphSelectionChanged(self):
        if self._glyphSelectionChangedCallback is not None:
            self._glyphSelectionChangedCallback(self)

    def setupFontItems(self):
        # clear all subviews
        for attr, value in list(self.__dict__.items()):
            if isinstance(value, VanillaBaseObject):
                delattr(self, attr)
        itemSize = self.itemSize
        y = 0
        for index, fontItemInfo in enumerate(self.project.fonts):
            fontItem = FontItem((0, y, 0, itemSize), fontItemInfo.fontKey, index)
            setattr(self, fontItemInfo.identifier, fontItem)
            y += itemSize
        self.setPosSize((0, 0, self.width, y))

    @property
    def width(self):
        return self.getPosSize()[2]

    @width.setter
    def width(self, newWidth):
        x, y, w, h = self.getPosSize()
        self.setPosSize((x, y, newWidth, h))

    @property
    def height(self):
        return self.getPosSize()[3]

    @height.setter
    def height(self, newHeight):
        x, y, w, h = self.getPosSize()
        self.setPosSize((x, y, w, newHeight))

    @hookedProperty
    def align(self):
        # self.align has already been set to the new value
        for fontItem in self.iterFontItems():
            fontItem.align = self.align

        scrollView = self._nsObject.enclosingScrollView()
        if scrollView is None:
            return

        ourBounds = self._nsObject.bounds()
        clipView = scrollView.contentView()
        clipBounds = clipView.bounds()
        if clipBounds.size.width >= ourBounds.size.width:
            # Handled by AligningScrollView
            return

        sizeDiff = ourBounds.size.width - clipBounds.size.width
        atLeft = abs(clipBounds.origin.x) < 2
        atRight = abs(clipBounds.origin.x - sizeDiff) < 2
        atCenter = abs(clipBounds.origin.x - sizeDiff / 2) < 2
        if self.align == "left":
            if atRight or atCenter:
                clipBounds.origin.x = 0
        elif self.align == "center":
            if atLeft or atRight:
                clipBounds.origin.x = sizeDiff / 2
        elif self.align == "right":
            if atLeft or atCenter:
                clipBounds.origin.x = sizeDiff
        clipView.setBounds_(clipBounds)

    def iterFontItems(self):
        if self.project is None:
            return
        for fontItemInfo in self.project.fonts:
            yield self.getFontItem(fontItemInfo.identifier)

    def iterFontItemInfoAndItems(self):
        if self.project is None:
            return
        for fontItemInfo in self.project.fonts:
            yield fontItemInfo, self.getFontItem(fontItemInfo.identifier)

    @hookedProperty
    def vertical(self):
        # Note that we heavily depend on hookedProperty's property that
        # the hook is only called when the value is different than before.
        vertical = self.vertical
        pos = [0, 0]
        for fontItem in self.iterFontItems():
            fontItem.vertical = vertical
            fontItem.fileNameLabel.setPosSize(fontItem.getFileNameLabelPosSize())
            fontItem.fileNameLabel.rotate([-90, 90][vertical])
            x, y, w, h = fontItem.getPosSize()
            w, h = h, w
            fontItem.setPosSize((*pos, w, h))
            pos[1 - vertical] += self.itemSize
        x, y, w, h = self.getPosSize()
        w, h = h, w
        self.setPosSize((x, y, w, h))
        self._nsObject.setNeedsDisplay_(True)

    @suppressAndLogException
    def resizeFontItems(self, itemSize):
        scaleFactor = itemSize / self.itemSize
        self.itemSize = itemSize
        pos = [0, 0]
        for fontItem in self.iterFontItems():
            x, y, *wh = fontItem.getPosSize()
            wh[1 - self.vertical] = itemSize
            fontItem.setPosSize((*pos, *wh))
            pos[1 - self.vertical] += itemSize

        # calculate the center of our clip view in relative doc coords
        # so we can set the scroll position and zoom in/out "from the middle"
        x, y, w, h = self.getPosSize()
        clipView = self._nsObject.superview()
        (cx, cy), (cw, ch) = clipView.bounds()
        cx += cw / 2
        cy -= ch / 2
        cx /= w
        cy /= h

        if not self.vertical:
            self.setPosSize((x, y, w * scaleFactor, pos[1]))
            cx *= w * scaleFactor
            cy *= pos[1]
        else:
            self.setPosSize((x, y, pos[0], h * scaleFactor))
            cx *= pos[0]
            cy *= h * scaleFactor
        cx -= cw / 2
        cy += ch / 2
        clipBounds = clipView.bounds()
        clipBounds.origin = (cx, cy)
        clipView.setBounds_(clipBounds)

    @suppressAndLogException
    def insertFonts(self, paths, index):
        addedIndices = []
        for fontPath, fontNumber in sortedFontPathsAndNumbers(paths, defaultSortSpec):
            self.project.addFont(fontPath, fontNumber, index)
            addedIndices.append(index)
            index += 1
        self.refitFontItems()
        self.scrollSelectionToVisible(addedIndices)

    def refitFontItems(self):
        itemSize = self.itemSize
        anyFontsToLoad = False
        for index, fontItemInfo in enumerate(self.project.fonts):
            fontItem = getattr(self, fontItemInfo.identifier, None)
            if fontItem is None:
                x, y, w, h = self.getPosSize()
                if self.vertical:
                    x = index * itemSize
                    w = itemSize
                    h = 0
                else:
                    y = index * itemSize
                    w = 0
                    h = itemSize
                fontItem = FontItem((x, y, w, h), fontItemInfo.fontKey, index)
                setattr(self, fontItemInfo.identifier, fontItem)
                if fontItemInfo.font is not None:
                    # Font is already loaded. TODO: rethink factorization? See below.
                    windowController = self._nsObject.window().windowController()
                    windowController.setFontItemText(fontItemInfo, fontItem)
                else:
                    anyFontsToLoad = True
            else:
                fontItem.fontListIndex = index
                x, y, w, h = fontItem.getPosSize()
                if self.vertical:
                    x = index * itemSize
                else:
                    y = index * itemSize
                fontItem.setPosSize((x, y, w, h))
        x, y, w, h = self.getPosSize()
        if self.vertical:
            w = len(self.project.fonts) * itemSize
        else:
            h = len(self.project.fonts) * itemSize
        self.setPosSize((x, y, w, h))
        if anyFontsToLoad:
            # TODO: rethink factorization?
            windowController = self._nsObject.window().windowController()
            windowController.loadFonts()

    @property
    def selection(self):
        return self._selection

    @selection.setter
    def selection(self, newSelection):
        diffSelection = self._selection ^ newSelection
        self._selection = newSelection
        for index in diffSelection:
            fontItem = self.getFontItem(self.project.fonts[index].identifier)
            fontItem.selected = not fontItem.selected
        if self._selectionChangedCallback is not None:
            self._selectionChangedCallback(self)

    def getFontItem(self, fontItemIdentifier):
        return getattr(self, fontItemIdentifier)

    def getNumFontItems(self):
        return len(self.project.fonts)

    def getSingleSelectedItem(self):
        if len(self.project.fonts) == 1:
            return self.getFontItem(self.project.fonts[0].identifier)
        elif len(self.selection) == 1:
            index = list(self.selection)[0]
            return self.getFontItem(self.project.fonts[index].identifier)
        else:
            return None

    def _getSelectionRect(self, selection):
        selRect = None
        for index in selection:
            fontItem = self.getFontItem(self.project.fonts[index].identifier)
            if selRect is None:
                selRect = fontItem._nsObject.frame()
            else:
                selRect = AppKit.NSUnionRect(selRect, fontItem._nsObject.frame())
        return selRect

    def scrollSelectionToVisible(self, selection=None):
        if selection is None:
            selection = self._selection
        self._nsObject.scrollRectToVisible_(self._getSelectionRect(selection))

    def scrollGlyphSelectionToVisible(self):
        if self.selection:
            fontItems = (self.getFontItem(self.project.fonts[index].identifier) for index in self.selection)
        else:
            fontItems = (self.getFontItem(fiInfo.identifier) for fiInfo in self.project.fonts)
        rects = []
        for fontItem in fontItems:
            view = fontItem.glyphLineView._nsObject
            x, y = fontItem._nsObject.frame().origin
            selRect = view.getSelectionRect()
            if selRect is not None:
                rects.append(AppKit.NSOffsetRect(selRect, x, y))
        if rects:
            selRect = rects[0]
            for rect in rects[1:]:
                selRect = AppKit.NSUnionRect(selRect, rect)
            self._nsObject.scrollRectToVisible_(selRect)

    @suppressAndLogException
    def mouseDown(self, event):
        glyphSelectionChanged = False
        index = self._lastItemClicked
        self._lastItemClicked = None
        if index is not None:
            fontItem = self.getFontItem(self.project.fonts[index].identifier)
            glyphSelectionChanged = bool(fontItem.popDiffSelection())
            clickedSelection = {index}
        else:
            for fontItem in self.iterFontItems():
                fontItem.selection = set()
            glyphSelectionChanged = True
            clickedSelection = set()

        if clickedSelection and event.modifierFlags() & AppKit.NSCommandKeyMask:
            newSelection = self._selection ^ clickedSelection
        elif index in self._selection:
            newSelection = None
        else:
            newSelection = clickedSelection
        if newSelection is not None:
            self.selection = newSelection
            if clickedSelection:
                self.scrollSelectionToVisible(clickedSelection)
        if glyphSelectionChanged:
            self._glyphSelectionChanged()

    @suppressAndLogException
    def keyDown(self, event):
        chars = event.characters()
        if chars in arrowKeyDefs:
            direction, vertical = arrowKeyDefs[chars]
            if vertical == self.vertical:
                if self._arrowKeyCallback is not None:
                    self._arrowKeyCallback(self, event)
                return

            numFontItems = len(self.project.fonts)
            if not self._selection:
                if direction == 1:
                    self.selection = {0}
                else:
                    self.selection = {numFontItems - 1}
            else:
                if direction == 1:
                    index = min(numFontItems - 1, max(self._selection) + 1)
                else:
                    index = max(0, min(self._selection) - 1)
                if event.modifierFlags() & AppKit.NSShiftKeyMask:
                    self.selection = self.selection | {index}
                else:
                    self.selection = {index}
                self.scrollSelectionToVisible()


class FontItem(Group):

    vertical = delegateProperty("glyphLineView")
    selected = delegateProperty("glyphLineView")

    def __init__(self, posSize, fontKey, fontListIndex):
        super().__init__(posSize)
        # self._nsObject.setWantsLayer_(True)
        # self._nsObject.setCanDrawSubviewsIntoLayer_(True)
        self.fontListIndex = fontListIndex
        self.glyphLineView = GlyphLine((0, 0, 0, 0))
        self.fileNameLabel = UnclickableTextBox(self.getFileNameLabelPosSize(), "", sizeStyle="small")
        self.progressSpinner = ProgressSpinner((10, 20, 25, 25))
        self.setFontKey(fontKey)

    def setIsLoading(self, isLoading):
        if isLoading:
            self.progressSpinner.start()
        else:
            self.progressSpinner.stop()

    def setFontKey(self, fontKey):
        fontPath, fontNumber = fontKey
        fileNameLabel = f"{fontPath.name}"
        if fontNumber or fontPath.suffix.lower() in {".ttc", ".otc"}:
            fileNameLabel += f"#{fontNumber}"
        self.fileNameLabel.set(fileNameLabel, tooltip=str(fontPath))

    @property
    def glyphs(self):
        return self.glyphLineView._nsObject._glyphs

    @glyphs.setter
    def glyphs(self, glyphs):
        self.glyphLineView._nsObject.glyphs = glyphs

    @property
    def selection(self):
        return self.glyphLineView._nsObject.selection

    @selection.setter
    def selection(self, newSelection):
        self.glyphLineView._nsObject.selection = newSelection

    def popDiffSelection(self):
        return self.glyphLineView._nsObject.popDiffSelection()

    @property
    def minimumExtent(self):
        return self.glyphLineView._nsObject.minimumExtent

    @property
    def align(self):
        return self.glyphLineView._nsObject.align

    @align.setter
    def align(self, value):
        if self.vertical:
            mapping = dict(top="left", center="center", bottom="right")
            value = mapping[value]
        self.fileNameLabel.align = value
        self.glyphLineView._nsObject.align = value

    def getFileNameLabelPosSize(self):
        if self.vertical:
            return (2, 10, 17, -10)
        else:
            return (10, 0, -10, 17)


class FGGlyphLineView(AppKit.NSView):

    def _scheduleRedraw(self):
        self.setNeedsDisplay_(True)

    selected = hookedProperty(_scheduleRedraw, default=False)
    align = hookedProperty(_scheduleRedraw, default="left")

    def init(self):
        self = super().init()
        self.vertical = 0  # 0, 1: it will also be an index into (x, y) tuples
        self._glyphs = None
        self._rectTree = None
        self._selection = set()
        self._hoveredGlyphIndex = None
        self._lastDiffSelection = None

        trackingArea = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            AppKit.NSTrackingActiveInKeyWindow | AppKit.NSTrackingMouseMoved |
            AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingInVisibleRect,
            self, None)
        self.addTrackingArea_(trackingArea)

        return self

    def isOpaque(self):
        return True

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse(self):
        return True

    def becomeFirstResponder(self):
        # Defer to our FGFontListView
        fontListView = self.superview().superview()
        assert isinstance(fontListView, FGFontListView)
        return fontListView.becomeFirstResponder()

    @property
    def selection(self):
        return self._selection

    @selection.setter
    def selection(self, newSelection):
        diffSelection = self._selection ^ newSelection
        self._selection = newSelection
        for index in diffSelection:
            bounds = self.getGlyphBounds_(index)
            if bounds is not None:
                self.setNeedsDisplayInRect_(bounds)
        self._lastDiffSelection = diffSelection

    @property
    def hoveredGlyphIndex(self):
        return self._hoveredGlyphIndex

    @hoveredGlyphIndex.setter
    def hoveredGlyphIndex(self, index):
        hoveredGlyphIndex = self._hoveredGlyphIndex
        if index == hoveredGlyphIndex:
            return
        prevBounds = self.getGlyphBounds_(hoveredGlyphIndex)
        newBounds = self.getGlyphBounds_(index)
        if prevBounds is None:
            bounds = newBounds
        elif newBounds is None:
            bounds = prevBounds
        else:
            bounds = AppKit.NSUnionRect(prevBounds, newBounds)
        self._hoveredGlyphIndex = index
        if bounds is not None:
            self.setNeedsDisplayInRect_(bounds)

    def getGlyphBounds_(self, index):
        if index is None or index >= len(self._glyphs):
            return None
        bounds = self._glyphs[index].bounds
        if bounds is None:
            return None
        dx, dy = self.origin
        scaleFactor = self.scaleFactor
        bounds = offsetRect(scaleRect(bounds, scaleFactor, scaleFactor), dx, dy)
        return nsRectFromRect(bounds)

    def getSelectionRect(self):
        """This methods returns an NSRect suitable for scrollRectToVisible_.
        It uses the "advance box" of selected glyphs, not the bounding box.
        """
        if not self._glyphs:
            return None
        scaleFactor = self.scaleFactor
        origin = self.origin
        extent = self.frame().size[1 - self.vertical]
        bounds = None
        for glyphIndex in self.selection:
            gi = self.glyphs[glyphIndex]
            pos = gi.pos[self.vertical] * scaleFactor + origin[self.vertical]
            adv = [gi.ax, gi.ay][self.vertical] * scaleFactor
            delta = [gi.dx, gi.dy][self.vertical] * scaleFactor
            if self.vertical:
                box = (0, pos - delta + adv, extent, pos - delta)
            else:
                box = (pos + delta, 0, pos + delta + adv, extent)
            if bounds is None:
                bounds = box
            else:
                bounds = unionRect(bounds, box)

        if bounds is None:
            return None
        dx, dy = self.origin
        return nsRectFromRect(bounds)

    def popDiffSelection(self):
        diffSelection = self._lastDiffSelection
        self._lastDiffSelection = None
        return diffSelection

    @property
    def glyphs(self):
        return self._glyphs

    @glyphs.setter
    def glyphs(self, glyphs):
        self._glyphs = glyphs
        rectIndexList = [(gi.bounds, index) for index, gi in enumerate(glyphs) if gi.bounds is not None]
        self._rectTree = RectTree.fromSeq(rectIndexList)
        self._selection = set()
        self._hoveredGlyphIndex = None  # no need to trigger smart redraw calculation
        self.setNeedsDisplay_(True)

    @property
    def minimumExtent(self):
        if self._glyphs is None:
            return self.margin * 2
        else:
            return self.margin * 2 + abs(self._glyphs.endPos[self.vertical]) * self.scaleFactor

    @property
    def scaleFactor(self):
        itemSize = self.frame().size[1 - self.vertical]
        return 0.7 * itemSize / self._glyphs.unitsPerEm

    @property
    def margin(self):
        itemSize = self.frame().size[1 - self.vertical]
        return 0.1 * itemSize

    @property
    def origin(self):
        endPos = abs(self._glyphs.endPos[self.vertical]) * self.scaleFactor
        margin = self.margin
        align = self.align
        itemExtent = self.frame().size[self.vertical]
        itemSize = self.frame().size[1 - self.vertical]
        if align == "right" or align == "bottom":
            pos = itemExtent - margin - endPos
        elif align == "center":
            pos = (itemExtent - endPos) / 2
        else:  # align == "left" or align == "top"
            pos = margin
        if not self.vertical:
            return pos, 0.25 * itemSize  # TODO: something with hhea/OS/2 ascender/descender
        else:
            return 0.5 * itemSize, itemExtent - pos  # TODO: something with vhea ascender/descender

    @suppressAndLogException
    def drawRect_(self, rect):
        backgroundColor = AppKit.NSColor.textBackgroundColor()
        foregroundColor = AppKit.NSColor.textColor()

        if self.selected:
            # Blend color could be a pref from the systemXxxxColor colors
            backgroundColor = backgroundColor.blendedColorWithFraction_ofColor_(
                0.5, AppKit.NSColor.selectedTextBackgroundColor())

        selection = self._selection
        hoveredGlyphIndex = self._hoveredGlyphIndex
        selectedColor = selectedSpaceColor = hoverColor = hoverSpaceColor = None
        if selection:
            selectedColor = foregroundColor.blendedColorWithFraction_ofColor_(
                0.9, AppKit.NSColor.systemRedColor())
            selectedSpaceColor = selectedColor.colorWithAlphaComponent_(0.2)
        if hoveredGlyphIndex is not None:
            hoverColor = AppKit.NSColor.systemBlueColor()
            if hoveredGlyphIndex in selection:
                hoverColor = hoverColor.blendedColorWithFraction_ofColor_(
                    0.5, selectedColor)
            hoverSpaceColor = hoverColor.colorWithAlphaComponent_(0.2)

        colors = {
            # (empty, selected, hovered)
            (0, 0, 0): foregroundColor,
            (0, 0, 1): hoverColor,
            (0, 1, 0): selectedColor,
            (0, 1, 1): hoverColor,
            (1, 0, 0): None,
            (1, 0, 1): hoverSpaceColor,
            (1, 1, 0): selectedSpaceColor,
            (1, 1, 1): hoverSpaceColor,
        }

        backgroundColor.set()
        AppKit.NSRectFill(rect)

        if not self._glyphs:
            return

        dx, dy = self.origin

        invScale = 1 / self.scaleFactor
        rect = rectFromNSRect(rect)
        rect = scaleRect(offsetRect(rect, -dx, -dy), invScale, invScale)

        translate(dx, dy)
        scale(self.scaleFactor)

        foregroundColor.set()
        lastPosX = lastPosY = 0
        for index in self._rectTree.iterIntersections(rect):
            gi = self._glyphs[index]
            selected = index in selection
            hovered = index == hoveredGlyphIndex
            empty = not gi.path.elementCount()
            posX, posY = gi.pos
            translate(posX - lastPosX, posY - lastPosY)
            lastPosX, lastPosY = posX, posY
            color = colors[empty, selected, hovered]
            if color is None:
                continue
            color.set()
            if empty:
                AppKit.NSRectFillUsingOperation(nsRectFromRect(offsetRect(gi.bounds, -posX, -posY)),
                                                AppKit.NSCompositeSourceOver)
            else:
                gi.path.fill()

    def mouseMoved_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.hoveredGlyphIndex = self.findGlyph_(point)

    def mouseEntered_(self, event):
        pass

    def mouseExited_(self, event):
        self.hoveredGlyphIndex = None

    @suppressAndLogException
    def mouseDown_(self, event):
        index = self.findGlyph_(self.convertPoint_fromView_(event.locationInWindow(), None))

        if not event.modifierFlags() & AppKit.NSCommandKeyMask:
            if index is None:
                newSelection = set()
            elif index in self.selection:
                newSelection = self.selection
            else:
                newSelection = {index}
            self.selection = newSelection

        # tell our parent we've been clicked on
        fontListIndex = self.superview().vanillaWrapper().fontListIndex
        fontList = self.superview().superview().vanillaWrapper()
        fontList._lastItemClicked = fontListIndex
        super().mouseDown_(event)

    def findGlyph_(self, point):
        if self._rectTree is None:
            return None

        x, y = point
        scaleFactor = self.scaleFactor
        dx, dy = self.origin
        x -= dx
        y -= dy
        x /= scaleFactor
        y /= scaleFactor

        indices = list(self._rectTree.iterIntersections((x, y, x, y)))
        if not indices:
            index = None
        elif len(indices) == 1:
            index = indices[0]
        else:
            # There are multiple candidates. Let's do point-inside testing,
            # and take the last hit, if any. Fall back to the last.
            for index in reversed(indices):
                gi = self._glyphs[index]
                posX, posY = gi.pos
                if gi.path.containsPoint_((x - posX, y - posY)):
                    break
            else:
                index = indices[-1]
        return index


class GlyphLine(Group):
    nsViewClass = FGGlyphLineView
    vertical = delegateProperty("_nsObject")
    selected = delegateProperty("_nsObject")


class FGUnclickableTextField(AppKit.NSTextField):

    def hitTest_(self, point):
        return None


class UnclickableTextBox(TextBox):

    """This TextBox sublass is transparent for clicks."""

    nsTextFieldClass = FGUnclickableTextField

    def __init__(self, *args, fontSize=12, **kwargs):
        super().__init__(*args, **kwargs)
        self._nsObject.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingMiddle)

    def set(self, value, tooltip=None):
        super().set(value)
        if tooltip is not None:
            self._nsObject.setToolTip_(tooltip)

    def rotate(self, angle):
        self._nsObject.rotateByAngle_(angle)

    @property
    def align(self):
        return self._nsObject.alignment()

    @align.setter
    def align(self, value):
        nsAlignment = textAlignments.get(value, textAlignments["left"])
        self._nsObject.cell().setAlignment_(nsAlignment)

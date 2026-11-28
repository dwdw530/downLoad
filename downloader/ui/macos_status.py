"""Menu-bar item attached to Tk's existing Cocoa event loop on the main thread."""
from AppKit import (NSImage, NSMenu, NSMenuItem, NSObject, NSStatusBar,
                    NSVariableStatusItemLength)


class DawStatusActions(NSObject):
    def showWindow_(self, sender):
        self.callbacks[0]()

    def exitApp_(self, sender):
        self.callbacks[1]()


class MacStatusItem:
    def __init__(self, image_path, show, quit_app):
        self.actions = DawStatusActions.alloc().init()
        self.actions.callbacks = (show, quit_app)
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        image = NSImage.alloc().initWithContentsOfFile_(image_path)
        if image:
            image.setSize_((18, 18))
            self.item.button().setImage_(image)
        else:
            self.item.button().setTitle_('↓')
        self.item.button().setToolTip_('daw下载器')
        menu = NSMenu.alloc().init()
        for title, action in [('显示主窗口', 'showWindow:'), ('退出下载器', 'exitApp:')]:
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, '')
            entry.setTarget_(self.actions)
            menu.addItem_(entry)
        self.item.setMenu_(menu)

    def stop(self):
        NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
